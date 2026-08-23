import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import type { SetStateAction } from "react";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  loading: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  dismiss: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
    POST: mocks.post,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    loading: mocks.loading,
    success: mocks.success,
    error: mocks.error,
    info: mocks.info,
    warning: mocks.warning,
    dismiss: mocks.dismiss,
  },
}));

import { fetchImportsPage, pruneQueuedScanIds } from "@/hooks/use-imports-queue";
import {
  IMPORTS_SSE_COALESCE_MS,
  cancelImportsListInvalidate,
  createImportsSseHandlers,
  scheduleImportsListInvalidate,
  syncImportProgressToast,
  syncScanStatusToast,
  triggerImportsScan,
} from "@/hooks/use-imports-queue-observation";
import {
  bulkImport,
  bulkRetry,
  ignoreItem,
  pickProviderCandidate,
  pickScanCandidate,
  resolveIntegrity,
  resolveTorrentRetry,
} from "@/hooks/use-imports-queue-actions";
import { qk } from "@/lib/query-keys";
import type {
  ImportItem,
  IntegrityImport,
  ScanCandidate,
  ScanImport,
  ScanProviderCandidate,
  StagedChoice,
  TorrentImport,
} from "@/lib/imports";
import type { components } from "@/lib/api/api";

type ScanRunStatus = components["schemas"]["ScanRunStatus"];

function createClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function scanItem(id: string, status: "pending" | "queued" | "imported"): ImportItem {
  return {
    kind: "scan",
    id,
    result: {
      directory: `/lib/${id}`,
      detected_name: id,
      library_name: "lib",
      size_bytes: 0,
      file_count: 0,
      files: [],
      status,
      candidates: [],
      provider_candidates: [],
    },
  };
}

function scanWithCandidate(id: string, candidate: ScanCandidate): ScanImport {
  return {
    kind: "scan",
    id,
    result: {
      directory: `/lib/${id}`,
      detected_name: id,
      library_name: "lib",
      size_bytes: 0,
      file_count: 0,
      files: [],
      status: "pending",
      candidates: [candidate],
      provider_candidates: [],
    },
  };
}

function torrentItem(id = "t1", title = "Some.Release.1080p"): TorrentImport {
  return {
    kind: "torrent",
    id,
    backoff_seconds: null,
    entry: {
      torrent_id: "00000000-0000-0000-0000-000000000001",
      torrent_title: title,
      torrent_status: 3,
      source_dir: "/data/downloads/some.release",
      progress: { total: 0, imported: 0, failed: 0, ambiguous: 0, pending: 0 },
      files: [],
    },
  };
}

function integrityItem(): IntegrityImport {
  return {
    kind: "integrity",
    id: "integrity:show:00000000-0000-0000-0000-0000000000aa",
    mismatch: {
      file_id: "00000000-0000-0000-0000-0000000000aa",
      media_type: "show",
      media_title: "Some Show",
      episode: "S01E01",
      path: "/data/tv/Some Show/Season 01/ep.mkv",
      quality: 2,
      variant_tag: "",
      import_error: "sha1 mismatch",
      detected_at: "2026-07-01T00:00:00Z",
    },
  };
}

function candidate(): ScanCandidate {
  return {
    media_type: "movie",
    media_id: "00000000-0000-0000-0000-000000000001",
    media_name: "Some Movie",
    confidence: 0.9,
  };
}

function providerCandidate(): ScanProviderCandidate {
  return {
    media_type: "movie",
    external_id: "tt123",
    metadata_provider: "tmdb",
    name: "New Movie",
    confidence: 0.8,
  };
}

describe("fetchImportsPage", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("requests a single page with tab/offset/limit and returns body total", async () => {
    const items = [scanItem("a", "pending")];
    mocks.get.mockResolvedValueOnce({
      data: { items, total: 321 },
      error: undefined,
    });

    const result = await fetchImportsPage("review", 3, 50);

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/api/v1/imports", {
      params: { query: { tab: "review", offset: 100, limit: 50 } },
      signal: undefined,
    });
    expect(result).toEqual({ items, total: 321 });
  });

  it("does not walk pages even when total far exceeds one page", async () => {
    const items = Array.from({ length: 50 }, (_, i) => scanItem(`s${i}`, "pending"));
    mocks.get.mockResolvedValueOnce({
      data: { items, total: 5000 },
      error: undefined,
    });

    const result = await fetchImportsPage("all", 1, 50);

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(result.items).toHaveLength(50);
    expect(result.total).toBe(5000);
  });

  it("rejects when openapi-fetch returns an error object", async () => {
    mocks.get.mockResolvedValueOnce({
      data: undefined,
      error: { detail: "boom" },
    });

    await expect(fetchImportsPage("review", 1, 50)).rejects.toEqual({ detail: "boom" });
  });

  it("returns empty items and total 0 for an empty page", async () => {
    mocks.get.mockResolvedValueOnce({
      data: { items: [], total: 0 },
      error: undefined,
    });

    const result = await fetchImportsPage("done", 1, 50);

    expect(result).toEqual({ items: [], total: 0 });
  });
});

describe("pruneQueuedScanIds", () => {
  it("keeps an id on the page with status pending", () => {
    const prev = new Set(["a"]);
    const next = pruneQueuedScanIds(prev, [scanItem("a", "pending")]);
    expect(next).toBe(prev);
    expect([...next]).toEqual(["a"]);
  });

  it("drops an id on the page with non-pending status", () => {
    const prev = new Set(["a", "b"]);
    const next = pruneQueuedScanIds(prev, [scanItem("a", "queued"), scanItem("b", "imported")]);
    expect(next).not.toBe(prev);
    expect([...next]).toEqual([]);
  });

  it("keeps an id absent from the current page (paging regression)", () => {
    const prev = new Set(["off-page"]);
    const next = pruneQueuedScanIds(prev, [scanItem("other", "pending")]);
    expect(next).toBe(prev);
    expect([...next]).toEqual(["off-page"]);
  });

  it("returns the same Set identity when nothing changes", () => {
    const prev = new Set(["a", "off"]);
    const next = pruneQueuedScanIds(prev, [scanItem("a", "pending")]);
    expect(next).toBe(prev);
  });
});

describe("queued-id reconciliation", () => {
  it("keeps optimistic ids until the on-page row is no longer pending", () => {
    const marked = new Set(["on-pending", "on-queued", "off-page"]);
    const next = pruneQueuedScanIds(marked, [
      scanItem("on-pending", "pending"),
      scanItem("on-queued", "queued"),
    ]);
    expect([...next].sort()).toEqual(["off-page", "on-pending"]);
  });
});

describe("250ms SSE list/counts coalescing", () => {
  let qc: QueryClient;
  let invalidate: ReturnType<typeof vi.spyOn>;
  let pending: { current: ReturnType<typeof setTimeout> | null };

  beforeEach(() => {
    vi.useFakeTimers();
    qc = createClient();
    invalidate = vi.spyOn(qc, "invalidateQueries");
    pending = { current: null };
  });

  afterEach(() => {
    cancelImportsListInvalidate(pending);
    vi.useRealTimers();
  });

  it("does not invalidate until 250ms after the first event", () => {
    scheduleImportsListInvalidate(pending, qc);
    scheduleImportsListInvalidate(pending, qc);
    scheduleImportsListInvalidate(pending, qc);

    vi.advanceTimersByTime(IMPORTS_SSE_COALESCE_MS - 1);
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.list() });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.counts() });
  });

  it("does not restart the window when events arrive while a timer is pending", () => {
    scheduleImportsListInvalidate(pending, qc);
    vi.advanceTimersByTime(200);
    scheduleImportsListInvalidate(pending, qc);
    vi.advanceTimersByTime(49);
    expect(invalidate).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it("clears the pending timer on dispose so unmount does not invalidate", () => {
    scheduleImportsListInvalidate(pending, qc);
    cancelImportsListInvalidate(pending);
    vi.advanceTimersByTime(IMPORTS_SSE_COALESCE_MS * 4);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("allows a new window after the previous one fires", () => {
    scheduleImportsListInvalidate(pending, qc);
    vi.advanceTimersByTime(IMPORTS_SSE_COALESCE_MS);
    expect(invalidate).toHaveBeenCalledTimes(2);
    scheduleImportsListInvalidate(pending, qc);
    vi.advanceTimersByTime(IMPORTS_SSE_COALESCE_MS);
    expect(invalidate).toHaveBeenCalledTimes(4);
  });
});

describe("SSE invalidation handlers", () => {
  let qc: QueryClient;
  let invalidate: ReturnType<typeof vi.spyOn>;
  let queue: ReturnType<typeof vi.fn<() => void>>;
  let handlers: ReturnType<typeof createImportsSseHandlers>;

  beforeEach(() => {
    qc = createClient();
    invalidate = vi.spyOn(qc, "invalidateQueries");
    queue = vi.fn<() => void>();
    handlers = createImportsSseHandlers(qc, queue);
  });

  it("import.updated with a torrent_id invalidates that torrent then coalesces the list", () => {
    handlers["import.updated"]({ torrent_id: "tor-1" }, "import.updated");
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.torrents.detail("tor-1") });
    expect(queue).toHaveBeenCalledOnce();
  });

  it("import.updated without a torrent_id only coalesces the list", () => {
    handlers["import.updated"]({}, "import.updated");
    expect(invalidate).not.toHaveBeenCalled();
    expect(queue).toHaveBeenCalledOnce();
  });

  it("torrent.refresh invalidates list and counts immediately", () => {
    handlers["torrent.refresh"](null, "torrent.refresh");
    expect(queue).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.list() });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.counts() });
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it("torrent.updated with an id invalidates that torrent then coalesces the list", () => {
    handlers["torrent.updated"]({ id: "tor-2" }, "torrent.updated");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.torrents.detail("tor-2") });
    expect(queue).toHaveBeenCalledOnce();
  });
});

describe("progress toast transitions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loading.mockReturnValue("import-toast");
  });

  it("opens a loading toast then updates it in place as the batch advances", () => {
    const refs = {
      toastId: { current: null as string | number | null },
      lastTotal: { current: 0 },
    };
    syncImportProgressToast(3, 5, refs);
    expect(mocks.loading).toHaveBeenCalledWith("Importing media · 2/5");
    expect(refs.toastId.current).toBe("import-toast");

    syncImportProgressToast(1, 5, refs);
    expect(mocks.loading).toHaveBeenCalledWith("Importing media · 4/5", { id: "import-toast" });
  });

  it("converts the loading toast to plural success when the batch drains", () => {
    const refs = {
      toastId: { current: null as string | number | null },
      lastTotal: { current: 0 },
    };
    syncImportProgressToast(2, 3, refs);
    syncImportProgressToast(0, 0, refs);
    expect(mocks.success).toHaveBeenCalledWith("Imported 3 items", { id: "import-toast" });
    expect(refs.toastId.current).toBeNull();
    expect(refs.lastTotal.current).toBe(0);
  });

  it("singularizes the success copy for one item", () => {
    const refs = {
      toastId: { current: null as string | number | null },
      lastTotal: { current: 0 },
    };
    syncImportProgressToast(1, 1, refs);
    syncImportProgressToast(0, 0, refs);
    expect(mocks.success).toHaveBeenCalledWith("Imported 1 item", { id: "import-toast" });
  });

  it("does nothing when idle with no live toast", () => {
    const refs = {
      toastId: { current: null as string | number | null },
      lastTotal: { current: 0 },
    };
    syncImportProgressToast(0, 0, refs);
    expect(mocks.loading).not.toHaveBeenCalled();
    expect(mocks.success).not.toHaveBeenCalled();
  });
});

describe("scan toast transitions", () => {
  let qc: QueryClient;
  let invalidate: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loading.mockReturnValue("scan-toast");
    qc = createClient();
    invalidate = vi.spyOn(qc, "invalidateQueries");
  });

  it("opens a loading toast when a scan starts", () => {
    const refs = { toastId: { current: null as string | number | null } };
    syncScanStatusToast("running", { state: "running", items_found: 0 }, refs, qc);
    expect(mocks.loading).toHaveBeenCalledWith("Scanning libraries…");
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("replaces the loading toast with success and invalidates all imports on done", () => {
    const refs = { toastId: { current: null as string | number | null } };
    syncScanStatusToast("running", { state: "running", items_found: 0 }, refs, qc);
    const data: ScanRunStatus = { state: "done", items_found: 4 };
    syncScanStatusToast("done", data, refs, qc);
    expect(mocks.success).toHaveBeenCalledWith("Scan complete · 4 candidate(s)", {
      id: "scan-toast",
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.all });
    expect(refs.toastId.current).toBeNull();
  });

  it("replaces the loading toast with the last_error copy on error", () => {
    const refs = { toastId: { current: null as string | number | null } };
    syncScanStatusToast("running", { state: "running", items_found: 0 }, refs, qc);
    syncScanStatusToast(
      "error",
      { state: "error", items_found: 0, last_error: "disk full" },
      refs,
      qc,
    );
    expect(mocks.error).toHaveBeenCalledWith("Scan failed: disk full", { id: "scan-toast" });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.all });
  });

  it("falls back to unknown when last_error is missing", () => {
    const refs = { toastId: { current: null as string | number | null } };
    syncScanStatusToast("running", { state: "running", items_found: 0 }, refs, qc);
    syncScanStatusToast("error", { state: "error", items_found: 0 }, refs, qc);
    expect(mocks.error).toHaveBeenCalledWith("Scan failed: unknown", { id: "scan-toast" });
  });

  it("dismisses the toast when returning to idle", () => {
    const refs = { toastId: { current: null as string | number | null } };
    syncScanStatusToast("running", { state: "running", items_found: 0 }, refs, qc);
    syncScanStatusToast("idle", { state: "idle", items_found: 0 }, refs, qc);
    expect(mocks.dismiss).toHaveBeenCalledWith("scan-toast");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.all });
  });
});

describe("triggerImportsScan", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("invalidates scan status after a successful start", async () => {
    mocks.post.mockResolvedValueOnce({ data: { state: "running" }, error: undefined });
    const qc = createClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    await triggerImportsScan(qc);
    expect(mocks.post).toHaveBeenCalledWith("/api/v1/imports/scan");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [...qk.imports.scan(), "status"] });
  });

  it("toasts when a scan is already running", async () => {
    mocks.post.mockResolvedValueOnce({
      data: { state: "running", detail: "scan already in progress" },
      error: undefined,
    });
    const qc = createClient();
    await triggerImportsScan(qc);
    expect(mocks.info).toHaveBeenCalledWith("Scan already running.");
  });

  it("toasts when the scan cannot start", async () => {
    mocks.post.mockResolvedValueOnce({ data: undefined, error: { detail: "nope" } });
    const qc = createClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    await triggerImportsScan(qc);
    expect(mocks.error).toHaveBeenCalledWith("Could not start scan.");
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("row and bulk invalidations", () => {
  let refreshAll: ReturnType<typeof vi.fn<() => void>>;
  let markQueued: ReturnType<typeof vi.fn<(ids: string[]) => void>>;
  let unmarkQueued: ReturnType<typeof vi.fn<(ids: string[]) => void>>;
  let setBusyId: ReturnType<typeof vi.fn<(id: string | null) => void>>;
  let setStagedByScan: ReturnType<
    typeof vi.fn<(value: SetStateAction<Record<string, StagedChoice>>) => void>
  >;

  beforeEach(() => {
    vi.clearAllMocks();
    refreshAll = vi.fn<() => void>();
    markQueued = vi.fn<(ids: string[]) => void>();
    unmarkQueued = vi.fn<(ids: string[]) => void>();
    setBusyId = vi.fn<(id: string | null) => void>();
    setStagedByScan = vi.fn<(value: SetStateAction<Record<string, StagedChoice>>) => void>();
    mocks.post.mockResolvedValue({ data: {}, error: undefined });
  });

  it("resolveTorrentRetry invalidates all imports on success", async () => {
    await resolveTorrentRetry(torrentItem(), { setBusyId, refreshAll });
    expect(mocks.success).toHaveBeenCalledWith('Queued retry for "Some.Release.1080p"', {
      description: "Re-import will run in the background.",
    });
    expect(refreshAll).toHaveBeenCalledOnce();
    expect(setBusyId.mock.calls).toEqual([["t1"], [null]]);
  });

  it("resolveTorrentRetry does not invalidate when the request fails", async () => {
    mocks.post.mockResolvedValueOnce({ error: { detail: "nope" } });
    await resolveTorrentRetry(torrentItem(), { setBusyId, refreshAll });
    expect(mocks.error).toHaveBeenCalledWith("Could not queue retry.");
    expect(refreshAll).not.toHaveBeenCalled();
  });

  it("pickScanCandidate marks queued, invalidates all, and unmarks on failure", async () => {
    const item = scanWithCandidate("s1", candidate());
    await pickScanCandidate(item, candidate(), { setBusyId, markQueued, unmarkQueued, refreshAll });
    expect(markQueued).toHaveBeenCalledWith(["s1"]);
    expect(refreshAll).toHaveBeenCalledOnce();
    expect(unmarkQueued).not.toHaveBeenCalled();

    mocks.post.mockResolvedValueOnce({ error: { detail: "nope" } });
    await pickScanCandidate(item, candidate(), { setBusyId, markQueued, unmarkQueued, refreshAll });
    expect(unmarkQueued).toHaveBeenCalledWith(["s1"]);
    expect(mocks.error).toHaveBeenCalledWith("Could not queue import.");
    expect(refreshAll).toHaveBeenCalledOnce();
  });

  it("pickProviderCandidate uses the library-entry toast copy and invalidates all", async () => {
    const item = scanWithCandidate("s1", candidate());
    await pickProviderCandidate(item, providerCandidate(), {
      setBusyId,
      markQueued,
      unmarkQueued,
      refreshAll,
    });
    expect(mocks.success).toHaveBeenCalledWith('Queued "s1" → New Movie', {
      description: "Library entry will be created and the import will run in the background.",
    });
    expect(refreshAll).toHaveBeenCalledOnce();
  });

  it("ignoreItem invalidates all after a confirmed torrent remove", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    await ignoreItem(torrentItem(), { setBusyId, refreshAll });
    expect(mocks.post).toHaveBeenCalledWith("/api/v1/imports/ignore", {
      body: { kind: "torrent", id: "t1", delete_files: true },
    });
    expect(mocks.success).toHaveBeenCalledWith("Torrent removed.");
    expect(refreshAll).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("ignoreItem does not invalidate when confirm is cancelled", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => false),
    );
    await ignoreItem(torrentItem(), { setBusyId, refreshAll });
    expect(mocks.post).not.toHaveBeenCalled();
    expect(refreshAll).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("resolveIntegrity rebaseline invalidates all after confirm", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    await resolveIntegrity(integrityItem(), "rebaseline", { setBusyId, refreshAll });
    expect(mocks.success).toHaveBeenCalledWith('Accepted current file for "Some Show".');
    expect(refreshAll).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("bulkRetry invalidates all after queueing torrent retries", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    await bulkRetry([torrentItem("t1"), torrentItem("t2", "Other")], { refreshAll });
    expect(mocks.success).toHaveBeenCalledWith("Queued retry for 2/2", {
      description: "Re-imports will run in the background.",
    });
    expect(refreshAll).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("bulkRetry does not invalidate when no torrents are selected", async () => {
    await bulkRetry([scanItem("s1", "pending")], { refreshAll });
    expect(mocks.info).toHaveBeenCalledWith("Select torrent rows to retry.");
    expect(refreshAll).not.toHaveBeenCalled();
  });

  it("bulkImport marks queued ids, invalidates all, and unmarks failures", async () => {
    const choice: StagedChoice = { kind: "candidate", data: candidate() };
    const ok = scanWithCandidate("ok", candidate());
    const fail = scanWithCandidate("fail", candidate());
    mocks.post
      .mockResolvedValueOnce({ data: {}, error: undefined })
      .mockResolvedValueOnce({ error: { detail: "nope" } });

    await bulkImport([ok, fail], {
      queuedScanIds: new Set(),
      effectiveChoiceFor: () => choice,
      markQueued,
      unmarkQueued,
      setStagedByScan,
      refreshAll,
    });

    expect(markQueued).toHaveBeenCalledWith(["ok", "fail"]);
    expect(unmarkQueued).toHaveBeenCalledWith(["fail"]);
    expect(mocks.success).toHaveBeenCalledWith("Queued 1 import", {
      description: "Imports will run in the background.",
    });
    expect(mocks.error).toHaveBeenCalledWith("1 import(s) could not be queued.");
    expect(refreshAll).toHaveBeenCalledOnce();
  });
});

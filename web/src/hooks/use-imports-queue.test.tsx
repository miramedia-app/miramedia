// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

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

vi.mock("@/hooks/use-event-stream", () => ({
  useEventStream: vi.fn(),
}));

import { useImportsQueue } from "@/hooks/use-imports-queue";
import { qk } from "@/lib/query-keys";
import type { ImportItem, ScanCandidate, ScanImport } from "@/lib/imports";

type IntervalQuery = { state: { data?: Record<string, unknown> } };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
}

function wrapperFor(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
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

function scanWithCandidate(id: string, dest: ScanCandidate): ScanImport {
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
      candidates: [dest],
      provider_candidates: [],
    },
  };
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

function importsGetCount() {
  return mocks.get.mock.calls.filter(([path]) => path === "/api/v1/imports").length;
}

function readInterval(qc: QueryClient, queryKey: readonly unknown[]) {
  const query = qc.getQueryCache().find({ queryKey });
  expect(query).toBeTruthy();
  const { refetchInterval } = query!.options;
  expect(typeof refetchInterval).toBe("function");
  return refetchInterval as (q: IntervalQuery) => number | false | undefined;
}

describe("useImportsQueue facade", () => {
  let listItems: ImportItem[];
  let scanStatus: { state: "idle" | "running" | "done" | "error"; items_found: number };
  let counts: {
    importing: number;
    import_total: number;
    review: number;
    retry: number;
    done: number;
    all: number;
  };

  beforeEach(() => {
    listItems = [];
    scanStatus = { state: "idle", items_found: 0 };
    counts = { importing: 0, import_total: 0, review: 0, retry: 0, done: 0, all: 0 };
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/api/v1/imports") {
        return { data: { items: listItems, total: listItems.length }, error: undefined };
      }
      if (path === "/api/v1/imports/scan/status") {
        return { data: scanStatus, error: undefined };
      }
      if (path === "/api/v1/imports/counts") {
        return { data: counts, error: undefined };
      }
      throw new Error(`unexpected GET ${path}`);
    });
    mocks.post.mockResolvedValue({ data: {}, error: undefined });
  });

  afterEach(() => {
    cleanup();
  });

  it("marks a scan queued before refetch and keeps the id when the row is off-page", async () => {
    const dest = candidate();
    const item = scanWithCandidate("s1", dest);
    listItems = [item];
    const qc = createClient();
    const { result } = renderHook(() => useImportsQueue(null, 1, 50), {
      wrapper: wrapperFor(qc),
    });

    await waitFor(() => expect(result.current.items).toEqual([item]));
    const getsBeforeAction = importsGetCount();

    const post = deferred<{ data: Record<string, never>; error: undefined }>();
    mocks.post.mockReturnValueOnce(post.promise);

    act(() => {
      void result.current.pickScanCandidate(item, dest);
    });

    await waitFor(() => expect(result.current.queuedScanIds.has("s1")).toBe(true));
    expect(importsGetCount()).toBe(getsBeforeAction);

    listItems = [scanItem("other", "pending")];
    post.resolve({ data: {}, error: undefined });

    await waitFor(() => expect(result.current.items.map((row) => row.id)).toEqual(["other"]));
    expect(result.current.queuedScanIds.has("s1")).toBe(true);
  });

  it("refreshAll invalidates the imports query-key prefix", async () => {
    const qc = createClient();
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useImportsQueue(null, 1, 50), {
      wrapper: wrapperFor(qc),
    });

    await waitFor(() => expect(result.current.scanState).toBe("idle"));
    invalidate.mockClear();

    act(() => {
      result.current.refreshAll();
    });

    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.imports.all });
  });

  it("polls scan status while running and counts while importing, and not when idle", async () => {
    const qc = createClient();
    renderHook(() => useImportsQueue(null, 1, 50), { wrapper: wrapperFor(qc) });

    await waitFor(() => {
      expect(qc.getQueryCache().find({ queryKey: [...qk.imports.scan(), "status"] })).toBeTruthy();
      expect(qc.getQueryCache().find({ queryKey: [...qk.imports.counts()] })).toBeTruthy();
    });

    const scanInterval = readInterval(qc, [...qk.imports.scan(), "status"]);
    expect(scanInterval({ state: { data: { state: "running" } } })).toEqual(expect.any(Number));
    expect(scanInterval({ state: { data: { state: "idle" } } })).toBe(false);

    const countsInterval = readInterval(qc, [...qk.imports.counts()]);
    expect(countsInterval({ state: { data: { importing: 2 } } })).toEqual(expect.any(Number));
    expect(countsInterval({ state: { data: { importing: 0 } } })).toBe(false);
  });
});

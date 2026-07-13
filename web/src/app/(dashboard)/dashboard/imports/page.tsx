"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertOctagon,
  Check,
  EllipsisVertical,
  FolderInput,
  LoaderCircle,
  Pencil,
  RefreshCw,
  RotateCcw,
  ScanLine,
  Trash2,
  X,
} from "lucide-react";
import { DashboardHeader } from "@/components/dashboard-header";
import { StatusPill } from "@/components/ui/status-pill";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import { getTorrentStatusString, qualityToString } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ManualMapDialog } from "@/components/imports/manual-map-dialog";
import { MatchConfidencePill } from "@/components/match-confidence-pill";
import { DataList } from "@/components/data-list";
import type { BulkAction, ColumnDef, FacetDef, GroupByDef } from "@/components/data-list";
import { useEventStream } from "@/hooks/use-event-stream";
import { useUser } from "@/components/providers/user-provider";
import apiClient from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import type { components } from "@/lib/api/api";

type TorrentImport = components["schemas"]["TorrentImportItem"];
type ScanImport = components["schemas"]["ScanImportItem"];
type MediaImport = components["schemas"]["MediaImportItem"];
type IntegrityMismatch = components["schemas"]["IntegrityMismatch"];
/** Integrity-audit mismatch (bit-rot) folded into the imports list as a row. */
type CorruptImport = { kind: "corrupt"; id: string; mismatch: IntegrityMismatch };
type ImportItem = TorrentImport | ScanImport | MediaImport | CorruptImport;
type ScanCandidate = components["schemas"]["ScanCandidate"];
type ScanProviderCandidate = components["schemas"]["ScanProviderCandidate"];
type ScanRunStatus = components["schemas"]["ScanRunStatus"];

const TRAILING_SLASHES = /\/+$/;
type ImportTabApi = "all" | "review" | "retry" | "done";

const BUCKET_ORDER: Record<string, number> = {
  Review: 0,
  Retry: 1,
  Corrupt: 2,
  Done: 3,
};

/**
 * The single kind vocabulary shared by grouping, facets and counts. Typed as an
 * exhaustive `Record` over `ImportItem["kind"]`, so adding a server kind fails
 * the typecheck here instead of silently falling into the "Downloads" bucket.
 */
const KIND_LABELS: Record<ImportItem["kind"], string> = {
  torrent: "Downloads",
  scan: "Scans",
  media: "Imported",
  corrupt: "Corrupt",
};

const KIND_ORDER: ImportItem["kind"][] = ["torrent", "scan", "media", "corrupt"];

function bucketOf(it: ImportItem): "Review" | "Retry" | "Corrupt" | "Done" {
  if (it.kind === "corrupt") return "Corrupt";
  if (it.kind === "scan") return it.result.status === "imported" ? "Done" : "Review";
  if (it.kind === "media") return "Done";
  const p = it.entry.progress;
  if ((p.failed ?? 0) > 0 || (p.ambiguous ?? 0) > 0) return "Review";
  if (p.imported >= p.total && p.total > 0) return "Done";
  if (it.backoff_seconds != null) return "Retry";
  return "Review";
}

/** Map search-bar Status facet (URL ``f`` param) to the imports API tab. */
function apiTabFromBucketFilter(filterParam: string | null): ImportTabApi {
  if (!filterParam) return "all";
  for (const segment of filterParam.split("&")) {
    if (!segment || segment.startsWith("!")) continue;
    const [facetId, rawValues = ""] = segment.split(":");
    if (facetId !== "bucket") continue;
    const value = decodeURIComponent(rawValues.split(",")[0]?.trim() ?? "");
    if (value === "Review") return "review";
    if (value === "Retry") return "retry";
    if (value === "Done") return "done";
    return "all";
  }
  return "all";
}

function isTorrent(item: ImportItem): item is TorrentImport {
  return item.kind === "torrent";
}

function isMedia(item: ImportItem): item is MediaImport {
  return item.kind === "media";
}

function isCorrupt(item: ImportItem): item is CorruptImport {
  return item.kind === "corrupt";
}

type RankedChoice =
  | { kind: "candidate"; data: ScanCandidate; confidence: number }
  | { kind: "provider"; data: ScanProviderCandidate; confidence: number };

// Memoize per scan-result reference. Same object identity (until a refetch
// replaces it) reuses the previous ranked list, avoiding the two-loop +
// sort each time the row renders (destination column + row actions).
const rankedCache = new WeakMap<ScanImport["result"], RankedChoice[]>();
function rankedChoices(r: ScanImport["result"]): RankedChoice[] {
  const cached = rankedCache.get(r);
  if (cached) return cached;
  const out: RankedChoice[] = [];
  for (const c of r.candidates ?? []) {
    out.push({ kind: "candidate", data: c, confidence: c.confidence ?? 0 });
  }
  for (const c of r.provider_candidates ?? []) {
    out.push({ kind: "provider", data: c, confidence: c.confidence ?? 0 });
  }
  out.sort((a, b) => b.confidence - a.confidence);
  rankedCache.set(r, out);
  return out;
}

export default function ImportsPage() {
  const qc = useQueryClient();
  const { user } = useUser();
  const searchParams = useSearchParams();
  const apiTab = React.useMemo(() => apiTabFromBucketFilter(searchParams.get("f")), [searchParams]);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  // Optimistically-removed corrupt rows: the action endpoints clear the
  // mismatch stamp but the list refetch lags the fast POST — hide the row
  // immediately and let the refetch reconcile.
  const [removedCorrupt, setRemovedCorrupt] = React.useState<Set<string>>(() => new Set());
  const [mapDialogTorrent, setMapDialogTorrent] = React.useState<{
    id: string;
    title: string;
  } | null>(null);
  const [candidateModalScan, setCandidateModalScan] = React.useState<ScanImport | null>(null);
  type StagedChoice =
    | { kind: "candidate"; data: ScanCandidate }
    | { kind: "provider"; data: ScanProviderCandidate };
  const [stagedByScan, setStagedByScan] = React.useState<Record<string, StagedChoice>>({});
  // Optimistically-queued scan ids. The resolve endpoint flips the cached scan
  // row to status "queued", but the list refetch lags the (fast, 202) POST —
  // without this bridge the stale "pending" row re-enables its Import button
  // for a beat and a double-click double-queues. Pruned once the server status
  // catches up (see reconcile effect below).
  const [queuedScanIds, setQueuedScanIds] = React.useState<Set<string>>(() => new Set());
  const scanToastIdRef = React.useRef<string | number | null>(null);
  // Live "Importing N/M" progress toast. M (the total) is server-side + durable
  // (``import_total``), so it survives a refresh and grows when more imports are
  // queued mid-batch. We keep the last total only to label the final success
  // toast, since the server resets it to 0 the instant the batch drains.
  const importToastIdRef = React.useRef<string | number | null>(null);
  const importLastTotalRef = React.useRef(0);

  const markQueued = React.useCallback((ids: string[]) => {
    setQueuedScanIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.add(id);
      return next;
    });
  }, []);
  const unmarkQueued = React.useCallback((ids: string[]) => {
    setQueuedScanIds((prev) => {
      if (!ids.some((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  }, []);

  const listQuery = useQuery({
    queryKey: [...qk.imports.list(apiTab), "all"],
    queryFn: async () => {
      const PAGE = 200; // server cap (le=200)
      const items: ImportItem[] = [];
      let offset = 0;
      let total = 0;
      // Hard ceiling: 20 pages (4k rows). The queue is tab-bucketed; hitting
      // this means something is wrong — bail with what we have.
      for (let i = 0; i < 20; i++) {
        const { data, error } = await apiClient.GET("/api/v1/imports", {
          params: { query: { tab: apiTab, offset, limit: PAGE } },
        });
        if (error) throw error;
        const page = (data?.items ?? []) as ImportItem[];
        items.push(...page);
        total = data?.total ?? items.length;
        offset += page.length;
        if (page.length < PAGE || items.length >= total) break;
      }
      return { items, total };
    },
    placeholderData: (prev) => prev,
  });

  const scanStatusQuery = useQuery<ScanRunStatus>({
    queryKey: [...qk.imports.scan(), "status"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/imports/scan/status");
      if (error) throw error;
      return data!;
    },
    // SSE drives import invalidation; this backstop only kicks in while a
    // scan is running and the event stream has dropped.
    refetchInterval: (q) => (q.state.data?.state === "running" ? 10000 : false),
    refetchIntervalInBackground: false,
  });

  // Background-import progress feed. ``importing`` = scan rows still queued for
  // a worker. SSE invalidates this key as rows complete; the interval is a
  // backstop while a batch is in flight in case the stream drops.
  const countsQuery = useQuery({
    queryKey: [...qk.imports.counts()],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/imports/counts");
      if (error) throw error;
      return data;
    },
    refetchInterval: (q) => ((q.state.data?.importing ?? 0) > 0 ? 5000 : false),
    refetchIntervalInBackground: false,
  });
  const importing = countsQuery.data?.importing ?? 0;
  const importTotal = countsQuery.data?.import_total ?? 0;

  // Integrity-audit mismatches (bit-rot) — folded into the list as "Corrupt" rows.
  // The endpoint is superuser-only, so gate the request on loaded user state:
  // while the user query is in flight `user` is null and this stays disabled,
  // which keeps ordinary users from ever provoking a 403.
  const canSeeIntegrity = Boolean(user?.is_superuser);
  const mismatchesQuery = useQuery({
    queryKey: qk.imports.integrity(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/torrents/integrity/mismatches");
      if (error) throw error;
      return data ?? [];
    },
    enabled: canSeeIntegrity,
  });

  React.useEffect(() => {
    if (importing > 0 && importTotal > 0) {
      importLastTotalRef.current = importTotal;
      const done = Math.max(0, importTotal - importing);
      const msg = `Importing media · ${done}/${importTotal}`;
      if (importToastIdRef.current == null) {
        importToastIdRef.current = toast.loading(msg);
      } else {
        toast.loading(msg, { id: importToastIdRef.current });
      }
    } else if (importToastIdRef.current != null) {
      const n = importLastTotalRef.current;
      toast.success(`Imported ${n} ${n === 1 ? "item" : "items"}`, {
        id: importToastIdRef.current,
      });
      importToastIdRef.current = null;
      importLastTotalRef.current = 0;
    }
  }, [importing, importTotal]);

  // Surgical SSE updates: an import event that names a torrent_id only
  // invalidates that torrent's detail key; the list is coalesced through a
  // single 250ms debounced refetch so 20 events/sec don't trigger 20
  // full-list refetches.
  const pendingImportsInvalidate = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const queueImportsListInvalidate = React.useCallback(() => {
    if (pendingImportsInvalidate.current) return;
    pendingImportsInvalidate.current = setTimeout(() => {
      pendingImportsInvalidate.current = null;
      void qc.invalidateQueries({ queryKey: qk.imports.list() });
      void qc.invalidateQueries({ queryKey: qk.imports.counts() });
    }, 250);
  }, [qc]);
  React.useEffect(() => {
    return () => {
      if (pendingImportsInvalidate.current) clearTimeout(pendingImportsInvalidate.current);
    };
  }, []);

  useEventStream({
    handlers: {
      "import.updated": (d: unknown) => {
        const torrentId = (d as { torrent_id?: string } | null)?.torrent_id;
        if (torrentId) void qc.invalidateQueries({ queryKey: qk.torrents.detail(torrentId) });
        queueImportsListInvalidate();
      },
      "torrent.refresh": () => {
        void qc.invalidateQueries({ queryKey: qk.imports.list() });
        void qc.invalidateQueries({ queryKey: qk.imports.counts() });
      },
      "torrent.updated": (d: unknown) => {
        const id = (d as { id?: string } | null)?.id;
        if (id) void qc.invalidateQueries({ queryKey: qk.torrents.detail(id) });
        queueImportsListInvalidate();
      },
    },
  });

  const items: ImportItem[] = React.useMemo(() => {
    const corrupt: CorruptImport[] = (mismatchesQuery.data ?? [])
      .map(
        (m): CorruptImport => ({
          kind: "corrupt",
          id: `corrupt:${m.media_type}:${m.file_id}`,
          mismatch: m,
        }),
      )
      .filter((c) => !removedCorrupt.has(c.id));
    return [...(listQuery.data?.items ?? []), ...corrupt];
  }, [listQuery.data, mismatchesQuery.data, removedCorrupt]);
  // A disabled query never reports `isLoading`/`isFetching`, so this stays false
  // for ordinary users. A failing mismatch fetch is reported separately instead
  // of being folded into the list error, so corruption trouble never blanks the
  // scan/torrent rows that loaded fine.
  const isLoading =
    listQuery.isLoading ||
    listQuery.isFetching ||
    mismatchesQuery.isLoading ||
    mismatchesQuery.isFetching;
  const integrityFailed = canSeeIntegrity && mismatchesQuery.isError;

  // Prune the optimistic-removed set once the server no longer returns those
  // rows, so a genuinely re-flagged file can reappear later.
  React.useEffect(() => {
    setRemovedCorrupt((prev) => {
      if (prev.size === 0) return prev;
      const present = new Set(
        (mismatchesQuery.data ?? []).map((m) => `corrupt:${m.media_type}:${m.file_id}`),
      );
      const next = new Set<string>();
      for (const k of prev) if (present.has(k)) next.add(k);
      return next.size === prev.size ? prev : next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mismatchesQuery.data]);

  async function resolveCorrupt(item: CorruptImport, action: "rebaseline" | "dismiss") {
    const m = item.mismatch;
    const msg =
      action === "rebaseline"
        ? `Accept the current file for "${m.media_title}"? Its checksum will be re-baselined from disk on the next audit.`
        : `Dismiss the mismatch for "${m.media_title}"? The original checksum is kept and re-verified on the next audit.`;
    if (!confirm(msg)) return;
    setBusyId(item.id);
    try {
      const path = { media_type: m.media_type, file_id: m.file_id };
      const { error } =
        action === "rebaseline"
          ? await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/rebaseline", {
              params: { path },
            })
          : await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/dismiss", {
              params: { path },
            });
      if (error) throw new Error("action failed");
      toast.success(
        action === "rebaseline"
          ? `Accepted current file for "${m.media_title}".`
          : `Dismissed mismatch for "${m.media_title}".`,
      );
      setRemovedCorrupt((prev) => new Set(prev).add(item.id));
      void qc.invalidateQueries({ queryKey: qk.imports.counts() });
      void mismatchesQuery.refetch();
    } catch {
      toast.error("Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  // Drop the optimistic "queued" flag once the server-side scan row has caught
  // up (status is no longer "pending") or the row has left the list — from then
  // on the real ``result.status`` drives the UI.
  React.useEffect(() => {
    setQueuedScanIds((prev) => {
      if (prev.size === 0) return prev;
      const stillPending = new Set<string>();
      for (const it of items) {
        if (it.kind === "scan" && it.result.status === "pending" && prev.has(it.id)) {
          stillPending.add(it.id);
        }
      }
      return stillPending.size === prev.size ? prev : stillPending;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listQuery.data]);

  const scanState = scanStatusQuery.data?.state ?? "idle";

  React.useEffect(() => {
    if (scanState === "running" && scanToastIdRef.current == null) {
      scanToastIdRef.current = toast.loading("Scanning libraries…");
    }
    if (scanState !== "running" && scanToastIdRef.current != null) {
      const found = scanStatusQuery.data?.items_found ?? 0;
      if (scanState === "done") {
        toast.success(`Scan complete · ${found} candidate(s)`, {
          id: scanToastIdRef.current,
        });
      } else if (scanState === "error") {
        toast.error(`Scan failed: ${scanStatusQuery.data?.last_error ?? "unknown"}`, {
          id: scanToastIdRef.current,
        });
      } else {
        toast.dismiss(scanToastIdRef.current);
      }
      scanToastIdRef.current = null;
      void qc.invalidateQueries({ queryKey: qk.imports.all });
    }
  }, [scanState, scanStatusQuery.data, qc]);

  function refreshAll() {
    void qc.invalidateQueries({ queryKey: qk.imports.all });
  }

  async function triggerScan() {
    const { data, error } = await apiClient.POST("/api/v1/imports/scan");
    if (error || !data) {
      toast.error("Could not start scan.");
      return;
    }
    if (data.state === "running" && data.detail === "scan already in progress") {
      toast.info("Scan already running.");
    }
    void qc.invalidateQueries({ queryKey: [...qk.imports.scan(), "status"] });
  }

  async function resolveTorrentRetry(item: TorrentImport) {
    setBusyId(item.id);
    try {
      const { error } = await apiClient.POST("/api/v1/imports/resolve", {
        body: { kind: "torrent", id: item.id, action: "retry" },
      });
      if (error) throw new Error("retry failed");
      toast.success(`Queued retry for "${item.entry.torrent_title}"`, {
        description: "Re-import will run in the background.",
      });
      refreshAll();
    } catch {
      toast.error("Could not queue retry.");
    } finally {
      setBusyId(null);
    }
  }

  async function pickScanCandidate(item: ScanImport, candidate: ScanCandidate) {
    setBusyId(item.id);
    markQueued([item.id]);
    try {
      const { error } = await apiClient.POST("/api/v1/imports/resolve", {
        body: {
          kind: "scan",
          id: item.id,
          media_type: candidate.media_type,
          media_id: candidate.media_id,
        },
      });
      if (error) throw new Error("import failed");
      toast.success(`Queued "${item.result.detected_name}" → ${candidate.media_name}`, {
        description: "Import will run in the background.",
      });
      refreshAll();
    } catch {
      unmarkQueued([item.id]);
      toast.error("Could not queue import.");
    } finally {
      setBusyId(null);
    }
  }

  async function pickProviderCandidate(item: ScanImport, candidate: ScanProviderCandidate) {
    setBusyId(item.id);
    markQueued([item.id]);
    try {
      const { error } = await apiClient.POST("/api/v1/imports/resolve", {
        body: {
          kind: "scan",
          id: item.id,
          media_type: candidate.media_type,
          external_id: candidate.external_id,
          metadata_provider: candidate.metadata_provider,
        },
      });
      if (error) throw new Error("import failed");
      toast.success(`Queued "${item.result.detected_name}" → ${candidate.name}`, {
        description: "Library entry will be created and the import will run in the background.",
      });
      refreshAll();
    } catch {
      unmarkQueued([item.id]);
      toast.error("Could not queue import.");
    } finally {
      setBusyId(null);
    }
  }

  async function ignoreItem(item: ImportItem) {
    // Media (torrent-independent Done) and corrupt rows are read-only — no ignore.
    if (isMedia(item) || isCorrupt(item)) return;
    const isTor = isTorrent(item);
    const label = isTor ? item.entry.torrent_title : item.result.detected_name;
    const msg = isTor
      ? `Remove torrent "${label}" and delete files?`
      : `Ignore "${item.result.directory}" forever?`;
    if (!confirm(msg)) return;
    setBusyId(item.id);
    try {
      const { error } = await apiClient.POST("/api/v1/imports/ignore", {
        body: { kind: item.kind, id: item.id, delete_files: isTor },
      });
      if (error) throw new Error("ignore failed");
      toast.success(isTor ? "Torrent removed." : "Path ignored.");
      refreshAll();
    } catch {
      toast.error("Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function bulkRetry(itemsToRetry: ImportItem[]) {
    const torrents = itemsToRetry.filter(isTorrent);
    if (torrents.length === 0) {
      toast.info("Select torrent rows to retry.");
      return;
    }
    if (!confirm(`Retry ${torrents.length} torrent(s)?`)) return;
    const results = await Promise.all(
      torrents.map((t) =>
        apiClient.POST("/api/v1/imports/resolve", {
          body: { kind: "torrent", id: t.id, action: "retry" },
        }),
      ),
    );
    const ok = results.filter((r) => !r.error).length;
    toast.success(`Queued retry for ${ok}/${torrents.length}`, {
      description: "Re-imports will run in the background.",
    });
    refreshAll();
  }

  // The destination a scan row will import into: an explicitly-staged choice if
  // the user picked one, otherwise the highest-confidence candidate.
  const effectiveChoiceFor = React.useCallback(
    (item: ScanImport): StagedChoice | null => {
      const staged = stagedByScan[item.id];
      if (staged) return staged;
      const top = rankedChoices(item.result)[0];
      if (!top) return null;
      return top.kind === "candidate"
        ? { kind: "candidate", data: top.data }
        : { kind: "provider", data: top.data };
    },
    [stagedByScan],
  );

  async function bulkImport(selected: ImportItem[]) {
    // Only scan rows that aren't already imported/queued and have a
    // destination to import into. (Torrent rows are handled by Retry.)
    const targets = selected
      .filter(
        (it): it is ScanImport =>
          it.kind === "scan" &&
          it.result.status !== "imported" &&
          it.result.status !== "queued" &&
          !queuedScanIds.has(it.id),
      )
      .map((it) => ({ it, choice: effectiveChoiceFor(it) }))
      .filter((x): x is { it: ScanImport; choice: StagedChoice } => x.choice !== null);

    if (targets.length === 0) {
      toast.info("Select scan rows with a destination to import.");
      return;
    }

    const ids = targets.map((t) => t.it.id);
    markQueued(ids);
    setStagedByScan((prev) => {
      const next = { ...prev };
      for (const id of ids) delete next[id];
      return next;
    });

    const results = await Promise.all(
      targets.map(({ it, choice }) =>
        apiClient.POST("/api/v1/imports/resolve", {
          body:
            choice.kind === "candidate"
              ? {
                  kind: "scan",
                  id: it.id,
                  media_type: choice.data.media_type,
                  media_id: choice.data.media_id,
                }
              : {
                  kind: "scan",
                  id: it.id,
                  media_type: choice.data.media_type,
                  external_id: choice.data.external_id,
                  metadata_provider: choice.data.metadata_provider,
                },
        }),
      ),
    );

    const failedIds = ids.filter((_, i) => results[i].error);
    if (failedIds.length > 0) unmarkQueued(failedIds);
    const ok = ids.length - failedIds.length;
    if (ok > 0) {
      toast.success(`Queued ${ok} import${ok === 1 ? "" : "s"}`, {
        description: "Imports will run in the background.",
      });
    }
    if (failedIds.length > 0) {
      toast.error(`${failedIds.length} import(s) could not be queued.`);
    }
    refreshAll();
  }

  const columns = React.useMemo<ColumnDef<ImportItem>[]>(
    () => [
      {
        id: "source",
        header: "Source",
        width: "minmax(240px,2fr)",
        render: (it) => {
          if (isCorrupt(it)) {
            const p = it.mismatch.path;
            const name = p?.split("/").filter(Boolean).pop() ?? "—";
            return (
              <span
                className="truncate pr-3 font-mono text-xs text-muted-foreground"
                title={p ?? undefined}
              >
                {name}
              </span>
            );
          }
          // Imported (cleaned-up) rows have no live source dir; show the
          // original torrent release name preserved in torrent_history.
          if (isMedia(it) && it.torrent_title) {
            return (
              <span
                className="truncate pr-3 font-mono text-xs text-muted-foreground"
                title={it.torrent_title}
              >
                {it.torrent_title}
              </span>
            );
          }
          const folder = isTorrent(it)
            ? it.entry.source_dir
            : isMedia(it)
              ? it.source_dir
              : it.result.directory;
          const name = folder?.replace(TRAILING_SLASHES, "").split("/").filter(Boolean).pop() ?? "";
          return (
            <span className="truncate pr-3 font-mono text-xs text-muted-foreground" title={folder}>
              {name || "—"}
            </span>
          );
        },
      },
      {
        id: "destination",
        header: "Destination",
        width: "minmax(240px,2fr)",
        render: (it) => {
          if (isCorrupt(it)) {
            const m = it.mismatch;
            return (
              <span className="truncate pr-3 text-sm">
                {m.media_title}
                {m.episode ? (
                  <span className="ml-1.5 font-mono text-xs text-muted-foreground">
                    {m.episode}
                  </span>
                ) : null}
              </span>
            );
          }
          if (isTorrent(it)) {
            const m = it.entry.media;
            return (
              <span className="truncate pr-3 text-sm">
                {m?.media_name ? (
                  <>
                    {m.media_name}
                    {m.media_year ? ` (${m.media_year})` : ""}
                  </>
                ) : (
                  <span className="text-muted-foreground">Unlinked</span>
                )}
              </span>
            );
          }
          if (isMedia(it)) {
            return (
              <span className="truncate pr-3 text-sm">
                {it.media_name}
                {it.media_year ? ` (${it.media_year})` : ""}
              </span>
            );
          }
          const r = it.result;
          if (r.imported_name) {
            return <span className="truncate pr-3 text-sm">{r.imported_name}</span>;
          }
          const staged = stagedByScan[it.id];
          const ranked = rankedChoices(r);
          const top = ranked[0];
          let label: string;
          let conf: number | null;
          if (staged?.kind === "candidate") {
            label = `${staged.data.media_name}${staged.data.media_year ? ` (${staged.data.media_year})` : ""}`;
            conf = staged.data.confidence;
          } else if (staged?.kind === "provider") {
            label = `${staged.data.name}${staged.data.year ? ` (${staged.data.year})` : ""}`;
            conf = staged.data.confidence;
          } else if (top?.kind === "candidate") {
            label = `${top.data.media_name}${top.data.media_year ? ` (${top.data.media_year})` : ""}`;
            conf = top.confidence;
          } else if (top?.kind === "provider") {
            label = `${top.data.name}${top.data.year ? ` (${top.data.year})` : ""}`;
            conf = top.confidence;
          } else {
            label = "Unmatched";
            conf = null;
          }
          return (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setCandidateModalScan(it);
              }}
              className={`mr-3 inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-muted ${
                staged ? "border-primary bg-primary/10" : ""
              }`}
              title="Choose destination"
            >
              <span className="max-w-[160px] truncate">{label}</span>
              {conf != null ? <MatchConfidencePill confidence={conf} /> : null}
            </button>
          );
        },
      },
      {
        id: "kind",
        header: "Type",
        width: "92px",
        render: (it) => (
          <TypePill>
            {it.kind === "corrupt" ? "File" : it.kind === "scan" ? "Scan" : "Torrent"}
          </TypePill>
        ),
      },
      {
        id: "progress",
        header: "Progress",
        width: "84px",
        hideBelow: "md",
        render: (it) => {
          if (isCorrupt(it)) {
            const m = it.mismatch;
            return (
              <div className="flex flex-wrap items-center gap-1">
                <MetaPill className="uppercase">{qualityToString(m.quality)}</MetaPill>
                {m.variant_tag ? <MetaPill className="font-mono">{m.variant_tag}</MetaPill> : null}
              </div>
            );
          }
          if (isTorrent(it) || isMedia(it)) {
            const p = isTorrent(it) ? it.entry.progress : it.progress;
            return (
              <div className="flex flex-wrap items-center gap-1">
                <MetaPill className="tabular-nums">
                  {p.imported}/{p.total}
                </MetaPill>
              </div>
            );
          }
          const r = it.result;
          const videos = r.files?.filter((f) => f.is_video).length ?? 0;
          if (videos === 0) {
            return <span className="text-muted-foreground">—</span>;
          }
          const label = videos === 1 ? "video" : "videos";
          return (
            <div className="flex flex-wrap items-center gap-1">
              <MetaPill className="tabular-nums" title={`${videos} ${label} in source dir`}>
                {`${videos} ${label}`}
              </MetaPill>
            </div>
          );
        },
      },
      {
        id: "status",
        header: "Status",
        width: "112px",
        hideBelow: "md",
        render: (it) => {
          if (isCorrupt(it)) {
            return (
              <div className="flex flex-wrap items-center gap-1">
                <StatusPill status="corrupt" title={it.mismatch.import_error} />
              </div>
            );
          }
          if (isTorrent(it)) {
            const p = it.entry.progress;
            // Reflect IMPORT outcome, not just the download state — a finished
            // download with a failed import must not read "Finished".
            let pill: React.ReactNode;
            if (p.failed > 0) {
              pill = <StatusPill status="failed" title={p.last_error ?? "Import failed"} />;
            } else if (p.ambiguous > 0) {
              pill = <StatusPill status="ambiguous" />;
            } else if (p.total > 0 && p.imported >= p.total) {
              pill = <StatusPill status="imported" />;
            } else {
              pill = <StatusPill status={getTorrentStatusString(it.entry.torrent_status)} />;
            }
            return <div className="flex flex-wrap items-center gap-1">{pill}</div>;
          }
          if (isMedia(it)) {
            const p = it.progress;
            const status = p.total > 0 && p.imported >= p.total ? "imported" : "pending";
            return (
              <div className="flex flex-wrap items-center gap-1">
                <StatusPill status={status} />
              </div>
            );
          }
          const r = it.result;
          const scanPill =
            r.status === "failed" ? (
              <StatusPill status="failed" title={r.import_error ?? "Import failed"} />
            ) : r.status === "imported" ? (
              <StatusPill status="imported" />
            ) : (
              <StatusPill status="pending" />
            );
          return <div className="flex flex-wrap items-center gap-1">{scanPill}</div>;
        },
      },
    ],
    [stagedByScan, setCandidateModalScan],
  );

  const bulkActions = React.useMemo<BulkAction<ImportItem>[]>(
    () => [
      {
        id: "import",
        label: "Import",
        icon: <FolderInput className="h-3.5 w-3.5" />,
        onRun: (items) => void bulkImport(items),
      },
      {
        id: "retry",
        label: "Retry",
        icon: <RotateCcw className="h-3.5 w-3.5" />,
        variant: "secondary",
        onRun: (items) => void bulkRetry(items),
      },
    ],
    // Re-memo when the staged/optimistic-queued state the handlers close over
    // changes; bulkImport/bulkRetry are recreated each render by design.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stagedByScan, queuedScanIds],
  );

  const groupings = React.useMemo<GroupByDef<ImportItem>[]>(
    () => [
      {
        id: "bucket",
        label: "Status",
        getGroup: (it) => {
          const b = bucketOf(it);
          return { key: b, label: b, sortOrder: BUCKET_ORDER[b] ?? 99 };
        },
      },
      {
        id: "kind",
        label: "Type",
        getGroup: (it) => ({
          key: it.kind,
          label: KIND_LABELS[it.kind],
          sortOrder: KIND_ORDER.indexOf(it.kind),
        }),
      },
    ],
    [],
  );

  const renderRowActions = React.useCallback(
    (it: ImportItem) => {
      const busy = busyId === it.id;
      if (isCorrupt(it)) {
        return (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void resolveCorrupt(it, "rebaseline")}
              title="Re-baseline the checksum from the file on disk next audit"
            >
              {busy ? (
                <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="mr-1 h-3.5 w-3.5" />
              )}
              Accept current
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              disabled={busy}
              onClick={() => void resolveCorrupt(it, "dismiss")}
              title="Keep the original checksum; re-verify next audit"
            >
              <X className="mr-1 h-3.5 w-3.5" />
              Dismiss
            </Button>
          </>
        );
      }
      if (isMedia(it)) return null;
      if (isTorrent(it)) {
        return (
          <>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              title="Map"
              onClick={() => setMapDialogTorrent({ id: it.id, title: it.entry.torrent_title })}
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              title="Retry"
              disabled={busy}
              onClick={() => void resolveTorrentRetry(it)}
            >
              {busy ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" />
              )}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                    <EllipsisVertical className="h-4 w-4" />
                  </Button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  className="text-destructive"
                  disabled={busy}
                  onClick={() => void ignoreItem(it)}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        );
      }
      const r = it.result;
      if (r.status === "imported") {
        return (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                className="text-destructive"
                disabled={busy}
                onClick={() => void ignoreItem(it)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Ignore
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        );
      }
      if (r.status === "queued" || queuedScanIds.has(it.id)) {
        return (
          <>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              Importing
            </div>
            {/* Reserve the trailing ⋮-menu slot so this lines up with the Import button column */}
            <div aria-hidden className="h-7 w-7 shrink-0" />
          </>
        );
      }
      const effective = effectiveChoiceFor(it);
      return (
        <>
          {effective ? (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => {
                setStagedByScan((prev) => {
                  const next = { ...prev };
                  delete next[it.id];
                  return next;
                });
                if (effective.kind === "candidate") {
                  void pickScanCandidate(it, effective.data);
                } else {
                  void pickProviderCandidate(it, effective.data);
                }
              }}
            >
              {busy ? <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
              Import
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                className="text-destructive"
                disabled={busy}
                onClick={() => void ignoreItem(it)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Ignore
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [busyId, stagedByScan, queuedScanIds, effectiveChoiceFor],
  );

  const facets = React.useMemo<FacetDef<ImportItem>[]>(
    () => [
      {
        id: "bucket",
        label: "Status",
        options: [
          { value: "Review", label: "Review" },
          { value: "Retry", label: "Retry" },
          { value: "Corrupt", label: "Corrupt" },
          { value: "Done", label: "Done" },
        ],
        predicate: (it, values, op) => {
          const hit = values.includes(bucketOf(it));
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "kind",
        label: "Type",
        options: KIND_ORDER.map((kind) => ({ value: kind, label: KIND_LABELS[kind] })),
        predicate: (it, values, op) => {
          const hit = values.includes(it.kind);
          return op === "excludes" ? !hit : hit;
        },
      },
    ],
    [],
  );

  return (
    <>
      <DashboardHeader
        crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Imports" }]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        {integrityFailed && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm">
            <div className="flex items-center gap-2">
              <AlertOctagon className="h-4 w-4 text-destructive" />
              <span>Corruption data could not be loaded. Other imports are unaffected.</span>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void mismatchesQuery.refetch()}
              disabled={mismatchesQuery.isFetching}
            >
              {mismatchesQuery.isFetching ? (
                <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
              )}
              Retry
            </Button>
          </div>
        )}
        <DataList<ImportItem>
          data={items}
          getId={(it) => it.id}
          columns={columns}
          pageSize={50}
          searchPlaceholder="Search imports…"
          searchMatch={(it, q) => {
            if (isCorrupt(it)) {
              return (
                it.mismatch.media_title.toLowerCase().includes(q) ||
                (it.mismatch.path ?? "").toLowerCase().includes(q)
              );
            }
            if (isTorrent(it)) {
              return (
                it.entry.torrent_title.toLowerCase().includes(q) ||
                (it.entry.media?.media_name ?? "").toLowerCase().includes(q)
              );
            }
            if (isMedia(it)) {
              return (
                it.media_name.toLowerCase().includes(q) ||
                it.torrent_title.toLowerCase().includes(q)
              );
            }
            return (
              it.result.detected_name.toLowerCase().includes(q) ||
              it.result.directory.toLowerCase().includes(q)
            );
          }}
          loading={isLoading && items.length === 0}
          density="rich"
          groupings={groupings}
          defaultGroupId="bucket"
          collapseStorageKey="imports"
          facets={facets}
          emptyIcon={<FolderInput />}
          emptyTitle="No imports yet"
          emptyDescription="Run a scan to surface library candidates."
          toolbarTrailing={
            <>
              <Button
                size="default"
                variant="outline"
                className="text-xs"
                onClick={() => void triggerScan()}
                disabled={scanState === "running"}
              >
                {scanState === "running" ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <ScanLine className="mr-1 h-4 w-4" />
                )}
                Scan
              </Button>
              <Button
                size="default"
                variant="outline"
                className="text-xs"
                onClick={refreshAll}
                disabled={isLoading}
              >
                {isLoading ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-4 w-4" />
                )}
                Refresh
              </Button>
            </>
          }
          bulkActions={bulkActions}
          expandedContent={(it) => {
            if (isCorrupt(it)) {
              const m = it.mismatch;
              return (
                <div className="flex flex-col gap-1 bg-black/30 px-4 py-3 text-xs text-muted-foreground">
                  {m.path ? (
                    <span className="truncate font-mono" title={m.path}>
                      {m.path}
                    </span>
                  ) : null}
                  <span className="truncate" title={m.import_error}>
                    {m.import_error}
                  </span>
                  <span>
                    Detected {m.detected_at ? new Date(m.detected_at).toLocaleString() : "—"}
                  </span>
                </div>
              );
            }
            if (isTorrent(it) || isMedia(it)) {
              const files = isTorrent(it) ? it.entry.files : it.files;
              if (files.length === 0) return null;
              return (
                <div className="bg-black/30 p-2">
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-2">
                    {files.map((file, i) => (
                      <div
                        key={`${file.media_label}-${i}`}
                        className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-3 py-2 text-xs"
                      >
                        <StatusPill
                          status={file.import_status}
                          label={file.import_status.startsWith("failed") ? "Failed" : undefined}
                          className="shrink-0"
                        />
                        <span className="shrink-0 font-mono">{file.media_label}</span>
                        {file.variant && (
                          <span className="shrink-0 font-mono text-muted-foreground">
                            · {file.variant}
                          </span>
                        )}
                        {file.import_error && (
                          <span className="ml-auto truncate text-red-500" title={file.import_error}>
                            {file.import_error}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              );
            }
            const r = it.result;
            const sfiles = r.files ?? [];
            if (sfiles.length === 0) {
              return (
                <div className="flex items-center justify-center bg-black/30 px-4 py-8 text-center text-xs text-muted-foreground">
                  No files listed — re-run the scan to refresh.
                </div>
              );
            }
            const scanFileStatus =
              r.status === "failed" ? "failed" : r.status === "imported" ? "imported" : "pending";
            return (
              <div className="bg-black/30 p-2">
                <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-2">
                  {sfiles.map((f, i) => (
                    <div
                      key={`${f.relative_path}-${i}`}
                      className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-3 py-2 text-xs"
                    >
                      <StatusPill
                        status={scanFileStatus}
                        className="h-5 shrink-0 px-1.5 text-[10px]"
                      />
                      <span className="truncate font-mono" title={f.relative_path}>
                        {f.relative_path}
                      </span>
                      <TypePill className="ml-auto h-5 shrink-0 px-1.5 text-[10px] uppercase">
                        {f.is_video ? "video" : "file"}
                      </TypePill>
                    </div>
                  ))}
                </div>
              </div>
            );
          }}
          rowActions={renderRowActions}
        />
      </main>

      {mapDialogTorrent && (
        <ManualMapDialog
          torrentId={mapDialogTorrent.id}
          torrentTitle={mapDialogTorrent.title}
          open={mapDialogTorrent !== null}
          onOpenChange={(open) => {
            if (!open) setMapDialogTorrent(null);
          }}
          onApplied={() => {
            setMapDialogTorrent(null);
            refreshAll();
          }}
        />
      )}

      <Dialog
        open={candidateModalScan !== null}
        onOpenChange={(open) => {
          if (!open) setCandidateModalScan(null);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Choose destination</DialogTitle>
          </DialogHeader>
          {candidateModalScan && (
            <div className="flex flex-col gap-3">
              <p className="text-xs text-muted-foreground">
                Pick a destination, then press Import on the row.
              </p>
              {(candidateModalScan.result.candidates ?? []).length === 0 &&
              (candidateModalScan.result.provider_candidates ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">No candidates found.</p>
              ) : (
                <div className="flex max-h-[60vh] flex-col gap-1.5 overflow-y-auto">
                  {rankedChoices(candidateModalScan.result).map((choice) => {
                    const scanId = candidateModalScan.id;
                    const staged = stagedByScan[scanId];
                    if (choice.kind === "candidate") {
                      const c = choice.data;
                      const isSelected =
                        staged?.kind === "candidate" &&
                        staged.data.media_type === c.media_type &&
                        staged.data.media_id === c.media_id;
                      return (
                        <button
                          key={`e-${c.media_type}-${c.media_id}`}
                          type="button"
                          disabled={busyId === scanId}
                          onClick={() => {
                            setStagedByScan((prev) => ({
                              ...prev,
                              [scanId]: { kind: "candidate", data: c },
                            }));
                            setCandidateModalScan(null);
                          }}
                          aria-pressed={isSelected}
                          className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted ${
                            isSelected
                              ? "border-primary bg-primary/10 hover:bg-primary/15"
                              : "bg-muted/50"
                          }`}
                        >
                          <span className="truncate">
                            {c.media_name}
                            {c.media_year ? ` (${c.media_year})` : ""}
                            <span className="ml-1 text-[10px] text-muted-foreground uppercase">
                              LIBRARY
                            </span>
                          </span>
                          <span className="ml-auto shrink-0">
                            <MatchConfidencePill
                              confidence={c.confidence}
                              breakdown={c.breakdown}
                            />
                          </span>
                        </button>
                      );
                    }
                    const c = choice.data;
                    const isSelected =
                      staged?.kind === "provider" &&
                      staged.data.metadata_provider === c.metadata_provider &&
                      staged.data.external_id === c.external_id;
                    return (
                      <button
                        key={`p-${c.metadata_provider}-${c.external_id}`}
                        type="button"
                        disabled={busyId === scanId}
                        onClick={() => {
                          setStagedByScan((prev) => ({
                            ...prev,
                            [scanId]: { kind: "provider", data: c },
                          }));
                          setCandidateModalScan(null);
                        }}
                        aria-pressed={isSelected}
                        className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted ${
                          isSelected
                            ? "border-primary bg-primary/10 hover:bg-primary/15"
                            : "bg-muted/50"
                        }`}
                      >
                        <span className="truncate">
                          {c.name}
                          {c.year ? ` (${c.year})` : ""}
                          <span className="ml-1 text-[10px] text-muted-foreground uppercase">
                            SEARCH
                          </span>
                        </span>
                        <span className="ml-auto shrink-0">
                          <MatchConfidencePill confidence={c.confidence} breakdown={c.breakdown} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

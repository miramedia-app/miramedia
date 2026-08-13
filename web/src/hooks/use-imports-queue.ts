"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useEventStream } from "@/hooks/use-event-stream";
import apiClient from "@/lib/api/client";
import { bulkMutate } from "@/lib/bulk-mutate";
import { qk } from "@/lib/query-keys";
import {
  apiTabFromBucketFilter,
  effectiveChoice,
  importsListViewState,
  isIntegrity,
  isMedia,
  isTorrent,
} from "@/lib/imports";
import type {
  ImportItem,
  ImportTabApi,
  IntegrityImport,
  ScanCandidate,
  ScanImport,
  ScanProviderCandidate,
  StagedChoice,
  TorrentImport,
} from "@/lib/imports";
import type { components } from "@/lib/api/api";

type ScanRunStatus = components["schemas"]["ScanRunStatus"];

/** Single-page imports list fetch — no client page-walking. */
export async function fetchImportsPage(
  apiTab: ImportTabApi,
  page: number,
  pageSize: number,
  signal?: AbortSignal,
): Promise<{ items: ImportItem[]; total: number }> {
  const { data, error } = await apiClient.GET("/api/v1/imports", {
    params: { query: { tab: apiTab, offset: (page - 1) * pageSize, limit: pageSize } },
    signal,
  });
  if (error) throw error;
  return { items: (data?.items ?? []) as ImportItem[], total: data?.total ?? 0 };
}

/**
 * Keep optimistic "queued" flags for ids absent from the current page (they may
 * live on another page under server paging). Drop only when the row is present
 * and no longer pending.
 */
export function pruneQueuedScanIds(prev: Set<string>, pageItems: ImportItem[]): Set<string> {
  if (prev.size === 0) return prev;
  const onPage = new Map<string, ScanImport>();
  for (const it of pageItems) {
    if (it.kind === "scan") onPage.set(it.id, it);
  }
  const next = new Set<string>();
  for (const id of prev) {
    const row = onPage.get(id);
    if (row === undefined || row.result.status === "pending") next.add(id);
  }
  return next.size === prev.size ? prev : next;
}

/**
 * Orchestrates the imports queue: list/scan/counts queries, SSE-driven
 * invalidation, progress/scan toasts, optimistic "queued" + staged-choice
 * state, and every resolve/ignore/retry/import mutation. The route page owns
 * only presentation (columns, dialogs, DataList) over the returned contract.
 *
 * Behavior — query keys, request order, debounce/polling timing, confirmation
 * wording, toast copy, and cache invalidation — is preserved exactly from the
 * original page implementation.
 */
export function useImportsQueue(filterParam: string | null, page: number, pageSize: number) {
  const qc = useQueryClient();
  const apiTab = React.useMemo(() => apiTabFromBucketFilter(filterParam), [filterParam]);

  const [busyId, setBusyId] = React.useState<string | null>(null);
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
    queryKey: qk.imports.list(apiTab, page, pageSize),
    queryFn: ({ signal }) => fetchImportsPage(apiTab, page, pageSize, signal),
    placeholderData: (prev) => prev,
  });

  const scanStatusQuery = useQuery<ScanRunStatus>({
    queryKey: [...qk.imports.scan(), "status"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/imports/scan/status", { signal });
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
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/imports/counts", { signal });
      if (error) throw error;
      return data;
    },
    refetchInterval: (q) => ((q.state.data?.importing ?? 0) > 0 ? 5000 : false),
    refetchIntervalInBackground: false,
  });
  const importing = countsQuery.data?.importing ?? 0;
  const importTotal = countsQuery.data?.import_total ?? 0;

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

  const items: ImportItem[] = React.useMemo(() => listQuery.data?.items ?? [], [listQuery.data]);
  const isLoading = listQuery.isLoading || listQuery.isFetching;
  const listView = importsListViewState({ isError: listQuery.isError, count: items.length });

  // Drop the optimistic "queued" flag once the server-side scan row on the
  // current page is no longer "pending". Ids absent from this page are kept —
  // under server paging they may still be pending on another page.
  React.useEffect(() => {
    setQueuedScanIds((prev) => pruneQueuedScanIds(prev, items));
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

  const refreshAll = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.imports.all });
  }, [qc]);

  const triggerScan = React.useCallback(async () => {
    const { data, error } = await apiClient.POST("/api/v1/imports/scan");
    if (error || !data) {
      toast.error("Could not start scan.");
      return;
    }
    if (data.state === "running" && data.detail === "scan already in progress") {
      toast.info("Scan already running.");
    }
    void qc.invalidateQueries({ queryKey: [...qk.imports.scan(), "status"] });
  }, [qc]);

  const resolveTorrentRetry = React.useCallback(
    async (item: TorrentImport) => {
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
    },
    [refreshAll],
  );

  const pickScanCandidate = React.useCallback(
    async (item: ScanImport, candidate: ScanCandidate) => {
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
    },
    [markQueued, unmarkQueued, refreshAll],
  );

  const pickProviderCandidate = React.useCallback(
    async (item: ScanImport, candidate: ScanProviderCandidate) => {
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
    },
    [markQueued, unmarkQueued, refreshAll],
  );

  // Corrupt-file rows resolve through the dedicated integrity endpoints, not
  // /imports/resolve: "rebaseline" accepts the on-disk file (checksum re-read
  // next audit), "dismiss" keeps the original checksum and re-verifies.
  const resolveIntegrity = React.useCallback(
    async (item: IntegrityImport, action: "rebaseline" | "dismiss") => {
      const m = item.mismatch;
      const msg =
        action === "rebaseline"
          ? `Accept the current file for "${m.media_title}"? Its checksum will be re-baselined from disk on the next audit.`
          : `Dismiss the mismatch for "${m.media_title}"? The original checksum is kept and re-verified on the next audit.`;
      if (!confirm(msg)) return;
      setBusyId(item.id);
      try {
        const params = { path: { media_type: m.media_type, file_id: m.file_id } };
        const { error } =
          action === "rebaseline"
            ? await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/rebaseline", {
                params,
              })
            : await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/dismiss", {
                params,
              });
        if (error) throw new Error("action failed");
        toast.success(
          action === "rebaseline"
            ? `Accepted current file for "${m.media_title}".`
            : `Dismissed mismatch for "${m.media_title}".`,
        );
        refreshAll();
      } catch {
        toast.error("Action failed.");
      } finally {
        setBusyId(null);
      }
    },
    [refreshAll],
  );

  const ignoreItem = React.useCallback(
    async (item: ImportItem) => {
      // Media rows (torrent-independent Done) and integrity rows (own actions)
      // have no ignore.
      if (isMedia(item) || isIntegrity(item)) return;
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
    },
    [refreshAll],
  );

  const bulkRetry = React.useCallback(
    async (itemsToRetry: ImportItem[]) => {
      const torrents = itemsToRetry.filter(isTorrent);
      if (torrents.length === 0) {
        toast.info("Select torrent rows to retry.");
        return;
      }
      if (!confirm(`Retry ${torrents.length} torrent(s)?`)) return;
      const { ok, failed } = await bulkMutate(torrents, (t) =>
        apiClient.POST("/api/v1/imports/resolve", {
          body: { kind: "torrent", id: t.id, action: "retry" },
        }),
      );
      if (failed === 0) {
        toast.success(`Queued retry for ${ok}/${torrents.length}`, {
          description: "Re-imports will run in the background.",
        });
      } else if (ok === 0) {
        toast.error(`${failed} retry request(s) failed.`);
      } else {
        toast.warning(`Queued retry for ${ok}/${torrents.length}, ${failed} failed`);
      }
      refreshAll();
    },
    [refreshAll],
  );

  // The destination a scan row will import into: an explicitly-staged choice if
  // the user picked one, otherwise the highest-confidence candidate.
  const effectiveChoiceFor = React.useCallback(
    (item: ScanImport): StagedChoice | null => effectiveChoice(item, stagedByScan[item.id]),
    [stagedByScan],
  );

  const bulkImport = React.useCallback(
    async (selected: ImportItem[]) => {
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

      const { failedItems } = await bulkMutate(targets, ({ it, choice }) =>
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
      );

      const failedIds = failedItems.map((t) => t.it.id);
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
    },
    [queuedScanIds, effectiveChoiceFor, markQueued, unmarkQueued, refreshAll],
  );

  return {
    items,
    totalCount: listQuery.data?.total,
    isLoading,
    listView,
    refetchList: listQuery.refetch,
    scanState,
    busyId,
    stagedByScan,
    setStagedByScan,
    queuedScanIds,
    effectiveChoiceFor,
    refreshAll,
    triggerScan,
    resolveTorrentRetry,
    pickScanCandidate,
    pickProviderCandidate,
    resolveIntegrity,
    ignoreItem,
    bulkRetry,
    bulkImport,
  };
}

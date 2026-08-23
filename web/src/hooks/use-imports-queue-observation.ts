"use client";

import * as React from "react";
import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useEventStream } from "@/hooks/use-event-stream";
import apiClient from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import type { ImportItem } from "@/lib/imports";
import type { components } from "@/lib/api/api";

type ScanRunStatus = components["schemas"]["ScanRunStatus"];

export const IMPORTS_SSE_COALESCE_MS = 250;

type TimeoutHandle = ReturnType<typeof setTimeout> | null;

export function scheduleImportsListInvalidate(
  pending: { current: TimeoutHandle },
  qc: QueryClient,
) {
  if (pending.current) return;
  pending.current = setTimeout(() => {
    pending.current = null;
    void qc.invalidateQueries({ queryKey: qk.imports.list() });
    void qc.invalidateQueries({ queryKey: qk.imports.counts() });
  }, IMPORTS_SSE_COALESCE_MS);
}

export function cancelImportsListInvalidate(pending: { current: TimeoutHandle }) {
  if (pending.current) clearTimeout(pending.current);
}

export function createImportsSseHandlers(
  qc: QueryClient,
  queueImportsListInvalidate: () => void,
): Record<string, (data: unknown, type: string) => void> {
  return {
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
  };
}

export type ImportProgressToastRefs = {
  toastId: { current: string | number | null };
  lastTotal: { current: number };
};

export function syncImportProgressToast(
  importing: number,
  importTotal: number,
  refs: ImportProgressToastRefs,
) {
  if (importing > 0 && importTotal > 0) {
    refs.lastTotal.current = importTotal;
    const done = Math.max(0, importTotal - importing);
    const msg = `Importing media · ${done}/${importTotal}`;
    if (refs.toastId.current == null) {
      refs.toastId.current = toast.loading(msg);
    } else {
      toast.loading(msg, { id: refs.toastId.current });
    }
  } else if (refs.toastId.current != null) {
    const n = refs.lastTotal.current;
    toast.success(`Imported ${n} ${n === 1 ? "item" : "items"}`, {
      id: refs.toastId.current,
    });
    refs.toastId.current = null;
    refs.lastTotal.current = 0;
  }
}

export type ScanToastRefs = {
  toastId: { current: string | number | null };
};

export function syncScanStatusToast(
  scanState: string,
  scanStatusData: ScanRunStatus | undefined,
  refs: ScanToastRefs,
  qc: QueryClient,
) {
  if (scanState === "running" && refs.toastId.current == null) {
    refs.toastId.current = toast.loading("Scanning libraries…");
  }
  if (scanState !== "running" && refs.toastId.current != null) {
    const found = scanStatusData?.items_found ?? 0;
    if (scanState === "done") {
      toast.success(`Scan complete · ${found} candidate(s)`, {
        id: refs.toastId.current,
      });
    } else if (scanState === "error") {
      toast.error(`Scan failed: ${scanStatusData?.last_error ?? "unknown"}`, {
        id: refs.toastId.current,
      });
    } else {
      toast.dismiss(refs.toastId.current);
    }
    refs.toastId.current = null;
    void qc.invalidateQueries({ queryKey: qk.imports.all });
  }
}

export async function triggerImportsScan(qc: QueryClient) {
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

/**
 * Keep optimistic "queued" flags for ids absent from the current page (they may
 * live on another page under server paging). Drop only when the row is present
 * and no longer pending.
 */
export function pruneQueuedScanIds(prev: Set<string>, pageItems: ImportItem[]): Set<string> {
  if (prev.size === 0) return prev;
  const onPage = new Map<string, Extract<ImportItem, { kind: "scan" }>>();
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

export function useImportsQueueObservation(
  qc: QueryClient,
  {
    importing,
    importTotal,
    scanState,
    scanStatusData,
    items,
    listQueryData,
  }: {
    importing: number;
    importTotal: number;
    scanState: string;
    scanStatusData: ScanRunStatus | undefined;
    items: ImportItem[];
    listQueryData: unknown;
  },
) {
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

  React.useEffect(() => {
    syncImportProgressToast(importing, importTotal, {
      toastId: importToastIdRef,
      lastTotal: importLastTotalRef,
    });
  }, [importing, importTotal]);

  // Surgical SSE updates: an import event that names a torrent_id only
  // invalidates that torrent's detail key; the list is coalesced through a
  // single 250ms debounced refetch so 20 events/sec don't trigger 20
  // full-list refetches.
  const pendingImportsInvalidate = React.useRef<TimeoutHandle>(null);
  const queueImportsListInvalidate = React.useCallback(() => {
    scheduleImportsListInvalidate(pendingImportsInvalidate, qc);
  }, [qc]);
  React.useEffect(() => {
    return () => {
      cancelImportsListInvalidate(pendingImportsInvalidate);
    };
  }, []);

  useEventStream({
    handlers: createImportsSseHandlers(qc, queueImportsListInvalidate),
  });

  // Drop the optimistic "queued" flag once the server-side scan row on the
  // current page is no longer "pending". Ids absent from this page are kept —
  // under server paging they may still be pending on another page.
  React.useEffect(() => {
    setQueuedScanIds((prev) => pruneQueuedScanIds(prev, items));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listQueryData]);

  React.useEffect(() => {
    syncScanStatusToast(scanState, scanStatusData, { toastId: scanToastIdRef }, qc);
  }, [scanState, scanStatusData, qc]);

  const triggerScan = React.useCallback(async () => {
    await triggerImportsScan(qc);
  }, [qc]);

  return { queuedScanIds, markQueued, unmarkQueued, triggerScan };
}

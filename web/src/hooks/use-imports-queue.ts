"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

// Backend caps `limit` at 200 per request, so the full list must be walked.
const IMPORTS_MAX_LIMIT = 200;

import { useImportsQueueActions } from "@/hooks/use-imports-queue-actions";
import {
  pruneQueuedScanIds,
  useImportsQueueObservation,
} from "@/hooks/use-imports-queue-observation";
import apiClient from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { apiTabFromBucketFilter, effectiveChoice, importsListViewState } from "@/lib/imports";
import type { ImportItem, ImportTabApi, ScanImport, StagedChoice } from "@/lib/imports";
import type { components } from "@/lib/api/api";

type ScanRunStatus = components["schemas"]["ScanRunStatus"];

export { pruneQueuedScanIds };

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
 * Full imports list (all pages) for the active tab. The list endpoint caps
 * `limit` at 200, so walk offsets until we've collected `total` rows. Used only
 * while a search is active so the DataList can match across every page instead
 * of the loaded server page.
 *
 * NOTE: this 1B approach loads the whole (filtered) list client-side. If the
 * import count grows large, switch to server-side `q` filtering (2B) — the
 * backend would add a `q` query param to /api/v1/imports and return matching
 * rows + total.
 */
export async function fetchAllImports(
  apiTab: ImportTabApi,
  signal?: AbortSignal,
): Promise<ImportItem[]> {
  const all: ImportItem[] = [];
  let offset = 0;
  for (;;) {
    const { data, error } = await apiClient.GET("/api/v1/imports", {
      params: { query: { tab: apiTab, offset, limit: IMPORTS_MAX_LIMIT } },
      signal,
    });
    if (error) throw error;
    const items = (data?.items ?? []) as ImportItem[];
    all.push(...items);
    const total = data?.total ?? all.length;
    offset += items.length;
    if (items.length === 0 || all.length >= total) break;
  }
  return all;
}

/**
 * Full-list fetch gated on `enabled` (search active), scoped to the active tab
 * so search stays within the tab. Kept separate from the paginated query so the
 * two never contend on one cache key.
 */
export function useImportsSearchAll(apiTab: ImportTabApi, enabled: boolean) {
  return useQuery({
    queryKey: qk.imports.searchAll(apiTab),
    queryFn: ({ signal }) => fetchAllImports(apiTab, signal),
    enabled,
    staleTime: 10_000,
  });
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

  const [stagedByScan, setStagedByScan] = React.useState<Record<string, StagedChoice>>({});

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

  const items: ImportItem[] = React.useMemo(() => listQuery.data?.items ?? [], [listQuery.data]);
  const isLoading = listQuery.isLoading || listQuery.isFetching;
  const listView = importsListViewState({ isError: listQuery.isError, count: items.length });
  const scanState = scanStatusQuery.data?.state ?? "idle";

  const { queuedScanIds, markQueued, unmarkQueued, triggerScan } = useImportsQueueObservation(qc, {
    importing,
    importTotal,
    scanState,
    scanStatusData: scanStatusQuery.data,
    items,
    listQueryData: listQuery.data,
  });

  const refreshAll = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.imports.all });
  }, [qc]);

  const effectiveChoiceFor = React.useCallback(
    (item: ScanImport): StagedChoice | null => effectiveChoice(item, stagedByScan[item.id]),
    [stagedByScan],
  );

  const {
    busyId,
    resolveTorrentRetry,
    pickScanCandidate,
    pickProviderCandidate,
    resolveIntegrity,
    ignoreItem,
    bulkRetry,
    bulkImport,
  } = useImportsQueueActions({
    refreshAll,
    markQueued,
    unmarkQueued,
    queuedScanIds,
    setStagedByScan,
    effectiveChoiceFor,
  });

  return {
    apiTab,
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

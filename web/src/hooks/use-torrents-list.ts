"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useEventStream } from "@/hooks/use-event-stream";
import apiClient from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { getTorrentStatusString } from "@/lib/utils";
import type { components } from "@/lib/api/api";

export type RichTorrent = components["schemas"]["RichTorrent"];

export type TorrentsListPage = {
  items: RichTorrent[];
  total: number | null;
};

const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 60000;

/** Poll while a download is active so the progress bar can move; otherwise a 60s SSE backstop. */
export function torrentsListRefetchInterval(page: TorrentsListPage | undefined): number {
  const hasActive = (page?.items ?? []).some(
    (t) => getTorrentStatusString(t.status) === "Downloading",
  );
  return hasActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

/** Single-page torrents list fetch — no client page-walking. */
export async function fetchTorrentsPage(
  page: number,
  pageSize: number,
  signal?: AbortSignal,
): Promise<TorrentsListPage> {
  const listRes = await apiClient.GET("/api/v1/torrents", {
    signal,
    params: {
      query: {
        limit: pageSize,
        offset: (page - 1) * pageSize,
        // Progress/peers/speed are not DB columns. Paginated live RPC is the
        // source of truth (same as the movie/show torrent tables).
        live: true,
      },
    },
  });
  if (listRes.error) throw listRes.error;
  const items = (listRes.data ?? []) as RichTorrent[];
  const raw = listRes.response?.headers?.get("x-total-count");
  const parsed = raw == null ? NaN : Number(raw);
  const total = Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  return { items, total };
}

/**
 * Server-paginated torrents dashboard query + SSE invalidation for the
 * current page. Search/sort/facets stay page-local in the DataList.
 */
export function useTorrentsList(page: number, pageSize: number) {
  const qc = useQueryClient();
  const listKey = React.useMemo(() => qk.torrents.list(page, pageSize), [page, pageSize]);

  const torrentsQuery = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) => fetchTorrentsPage(page, pageSize, signal),
    placeholderData: (prev) => prev,
    // Live RPC on each fetch; poll at 5s while something is downloading so
    // the progress bar moves. 60s otherwise, with SSE as the primary
    // invalidation path when the stream is healthy.
    refetchInterval: (q) => torrentsListRefetchInterval(q.state.data),
    refetchIntervalInBackground: false,
  });

  // Surgical SSE updates: avoid a refetch storm when many torrents tick at
  // once. `torrent.updated` / `import.updated` coalesce a single list refetch
  // via a 250ms debounce. Create/delete still bust immediately.
  const pendingListInvalidate = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const queueListInvalidate = React.useCallback(() => {
    if (pendingListInvalidate.current) return;
    pendingListInvalidate.current = setTimeout(() => {
      pendingListInvalidate.current = null;
      void qc.invalidateQueries({ queryKey: qk.torrents.list() });
    }, 250);
  }, [qc]);
  React.useEffect(() => {
    return () => {
      if (pendingListInvalidate.current) clearTimeout(pendingListInvalidate.current);
    };
  }, []);

  useEventStream({
    handlers: {
      "torrent.updated": (d: unknown) => {
        const id = (d as { id?: string } | null)?.id;
        if (id) void qc.invalidateQueries({ queryKey: qk.torrents.detail(id) });
        queueListInvalidate();
      },
      "torrent.created": () => {
        void qc.invalidateQueries({ queryKey: qk.torrents.list() });
      },
      "torrent.deleted": (d: unknown) => {
        const id = (d as { id?: string } | null)?.id;
        void qc.invalidateQueries({ queryKey: qk.torrents.list() });
        if (id) qc.removeQueries({ queryKey: qk.torrents.detail(id) });
      },
      "torrent.refresh": () => {
        void qc.invalidateQueries({ queryKey: qk.torrents.list() });
      },
      "import.updated": (d: unknown) => {
        const torrentId = (d as { torrent_id?: string } | null)?.torrent_id;
        if (torrentId) void qc.invalidateQueries({ queryKey: qk.torrents.detail(torrentId) });
        queueListInvalidate();
      },
    },
  });

  return torrentsQuery;
}

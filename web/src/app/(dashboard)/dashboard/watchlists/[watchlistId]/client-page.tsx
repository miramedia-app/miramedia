"use client";

import { WatchlistDetail } from "@/components/watchlists/watchlist-detail";
import { WatchlistsPageShell } from "@/components/watchlists/watchlists-nav";
import { useWatchlist } from "@/hooks/use-watchlists";
import { useRouteUuid } from "@/lib/use-route-id";

export default function WatchlistDetailClientPage() {
  const watchlistId = useRouteUuid("watchlistId");
  const detailQuery = useWatchlist(watchlistId ?? "", !!watchlistId);
  const listName = detailQuery.data?.name ?? "List";

  return (
    <WatchlistsPageShell crumbs={[{ label: listName }]}>
      {watchlistId ? <WatchlistDetail watchlistId={watchlistId} /> : null}
    </WatchlistsPageShell>
  );
}

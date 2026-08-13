"use client";

import { WatchNextDetail } from "@/components/watchlists/watch-next-detail";
import { WatchlistsPageShell } from "@/components/watchlists/watchlists-nav";
import { WATCH_NEXT_LABEL } from "@/components/watchlists/watchlists-routes";

export default function WatchNextPage() {
  return (
    <WatchlistsPageShell crumbs={[{ label: WATCH_NEXT_LABEL }]}>
      <WatchNextDetail />
    </WatchlistsPageShell>
  );
}

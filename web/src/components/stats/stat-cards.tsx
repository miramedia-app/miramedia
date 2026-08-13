"use client";

import Link from "next/link";
import { AnimatedCard } from "./animated-card";
import { useUser } from "@/components/providers/user-provider";

function StatCardSkeleton() {
  return (
    <div
      className="rounded-xl border bg-card p-6 shadow-sm"
      aria-hidden="true"
      data-slot="stat-card-skeleton"
    >
      <div className="mb-4 h-4 w-20 rounded bg-muted/40" />
      <div className="mb-4 h-9 w-16 rounded bg-muted/40" />
      <div className="h-3 w-28 rounded bg-muted/30" />
    </div>
  );
}

type StatCardsReady = {
  isLoading?: false;
  showCount: number;
  moviesCount: number;
  torrentCount: number;
  requestCount: number;
};

type StatCardsLoading = {
  isLoading: true;
  showCount?: never;
  moviesCount?: never;
  torrentCount?: never;
  requestCount?: never;
};

export function StatCards(props: StatCardsReady | StatCardsLoading) {
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;
  if (!isSuperuser) return null;

  if (props.isLoading) {
    return (
      <div
        className="grid grid-cols-2 gap-4 lg:grid-cols-4"
        aria-busy="true"
        aria-label="Loading dashboard counts"
      >
        <StatCardSkeleton />
        <StatCardSkeleton />
        <StatCardSkeleton />
        <StatCardSkeleton />
      </div>
    );
  }

  const { showCount, moviesCount, torrentCount, requestCount } = props;
  const showFooter = "Total tracked shows";
  const movieFooter = "Total tracked movies";

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Link href="/dashboard/shows" className="transition-opacity hover:opacity-80">
        <AnimatedCard title="Shows" footer={showFooter} number={showCount} />
      </Link>
      <Link href="/dashboard/movies" className="transition-opacity hover:opacity-80">
        <AnimatedCard title="Movies" footer={movieFooter} number={moviesCount} />
      </Link>
      <Link href="/dashboard/requests" className="transition-opacity hover:opacity-80">
        <AnimatedCard title="Requests" footer="Pending media requests" number={requestCount} />
      </Link>
      <Link href="/dashboard/torrents" className="transition-opacity hover:opacity-80">
        <AnimatedCard title="Torrents" footer="Active torrents/NZBs" number={torrentCount} />
      </Link>
    </div>
  );
}

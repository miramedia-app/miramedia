"use client";

import Link from "next/link";
import { AnimatedCard } from "./animated-card";
import { useUser } from "@/components/providers/user-provider";

export function StatCards({
  showCount,
  moviesCount,
  torrentCount,
  requestCount,
}: {
  showCount: number;
  moviesCount: number;
  torrentCount: number;
  requestCount: number;
}) {
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;
  if (!isSuperuser) return null;

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

"use client";

import { StatusPill } from "@/components/ui/status-pill";

export type MediaStatus = "skipped" | "wanted" | "downloaded";

/**
 * Show/movie/episode availability pill. Thin wrapper over {@link StatusPill}
 * so it shares the app-wide status styling (monochrome — tone, not color).
 */
export function MediaStatusBadge({
  status,
  className,
}: {
  status: MediaStatus;
  className?: string;
}) {
  return <StatusPill status={status} className={className} />;
}

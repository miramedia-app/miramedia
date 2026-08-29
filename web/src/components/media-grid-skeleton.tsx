"use client";

import * as React from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { MEDIA_GRID_COLUMNS_CLASS, MEDIA_GRID_GAP_CLASS } from "@/components/virtual-media-grid";

/** Poster-grid loading placeholder.
 *
 * - `layout="default"` matches the shows/movies library + add cards
 *   (poster + year/title/button text rows).
 * - `layout="compact"` is poster-only (denser grid). */
/** Ladder for fixed-count dashboard rows (5 items max — never more columns than items). */
export const MEDIA_ROW_COLUMNS_CLASS = "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5";

export function MediaGridSkeleton({
  count = 10,
  layout = "default",
  columnsClass = MEDIA_GRID_COLUMNS_CLASS,
}: {
  count?: number;
  layout?: "default" | "compact";
  /** Override the column ladder so the skeleton matches the grid it replaces. */
  columnsClass?: string;
}) {
  if (layout === "compact") {
    return (
      <div className={`grid w-full ${MEDIA_GRID_GAP_CLASS} ${columnsClass}`}>
        {Array.from({ length: count }).map((_, i) => (
          <Skeleton key={i} className="aspect-[2/3] w-full rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div className={`grid w-full auto-rows-min ${MEDIA_GRID_GAP_CLASS} ${columnsClass}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex flex-col">
          <Skeleton className="aspect-[2/3] w-full rounded-lg" />
          <div className="flex flex-col gap-1.5 py-2">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="mt-3 h-9 w-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

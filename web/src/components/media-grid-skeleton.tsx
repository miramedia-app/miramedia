"use client";

import * as React from "react";
import { Skeleton } from "@/components/ui/skeleton";

/** Poster-grid loading placeholder.
 *
 * - `layout="default"` matches the shows/movies library + add cards
 *   (poster + year/title/button text rows).
 * - `layout="compact"` is poster-only (denser grid). */
export function MediaGridSkeleton({
  count = 10,
  layout = "default",
}: {
  count?: number;
  layout?: "default" | "compact";
}) {
  if (layout === "compact") {
    return (
      <div className="grid w-full gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {Array.from({ length: count }).map((_, i) => (
          <Skeleton key={i} className="aspect-[2/3] w-full rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div className="grid w-full auto-rows-min gap-4 sm:grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
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

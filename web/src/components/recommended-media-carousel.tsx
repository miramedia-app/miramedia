"use client";

import Link from "next/link";
import { ChevronRight, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { components } from "@/lib/api/api";
import { AddMediaCard } from "./add-media-card";
import { MediaGridSkeleton } from "./media-grid-skeleton";

type SearchResult = components["schemas"]["MetaDataProviderSearchResult"];

export function RecommendedMediaCarousel({
  media,
  isShow,
  isLoading,
  title,
  errorMessage = null,
}: {
  media: SearchResult[];
  isShow: boolean;
  isLoading: boolean;
  title: string;
  errorMessage?: string | null;
}) {
  return (
    <>
      <div className="flex items-center">
        <h3 className="text-2xl font-semibold">{title}</h3>
        <Button
          render={<Link href={isShow ? "/dashboard/shows/add" : "/dashboard/movies/add"} />}
          className="ml-auto"
          variant="link"
        >
          More recommendations
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
      {errorMessage ? (
        <div className="relative w-full">
          <div className="grid w-full gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            <div className="pointer-events-none aspect-[2/3]" />
          </div>
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-center">
            <TriangleAlert className="h-8 w-8 text-muted-foreground" />
            <p className="max-w-xs text-sm text-muted-foreground">{errorMessage}</p>
            <Button variant="outline" onClick={() => location.reload()}>
              Retry
            </Button>
          </div>
        </div>
      ) : isLoading ? (
        <MediaGridSkeleton count={5} />
      ) : (
        <div className="grid w-full gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {media
            .slice(0, 5)
            // First card is above-the-fold LCP candidate — fetch high.
            .map((m, i) => (
              <AddMediaCard key={m.external_id} result={m} isShow={isShow} priority={i === 0} />
            ))}
        </div>
      )}
    </>
  );
}

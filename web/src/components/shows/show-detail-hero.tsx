"use client";

import * as React from "react";
import dynamic from "next/dynamic";

import { Badge } from "@/components/ui/badge";
import { MediaPicture } from "@/components/media-picture";
import { MediaStatusBadge } from "@/components/media-status-badge";
import { MediaActionsMenu } from "@/components/media-actions-menu";
import type { ShowDetail } from "@/hooks/use-show-detail";

const ShowSettingsSheet = dynamic(
  () =>
    import("@/components/shows/show-settings-sheet").then((m) => ({
      default: m.ShowSettingsSheet,
    })),
  { ssr: false },
);

export interface ShowDetailHeroProps {
  show: ShowDetail;
  isSuperuser: boolean;
}

/** Poster + metadata header for the show detail page. */
export function ShowDetailHero({ show, isSuperuser }: ShowDetailHeroProps) {
  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-stretch">
      <div className="w-[8.8rem] shrink-0 overflow-hidden rounded-xl md:w-44">
        <MediaPicture media={show} />
      </div>
      <div className="flex flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <MediaStatusBadge status={show.status ?? (show.skipped ? "skipped" : "wanted")} />
        </div>
        <h1 className="line-clamp-1 text-2xl font-bold tracking-tight">{show.name}</h1>
        {show.content_rating && (
          <Badge variant="outline" className="w-fit font-mono text-xs">
            {show.content_rating}
          </Badge>
        )}
        {show.overview && (
          <p className="mt-1 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
            {show.overview}
          </p>
        )}
        {show.genres && show.genres.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {show.genres.map((g) => (
              <Badge key={g} variant="secondary" className="text-xs">
                {g}
              </Badge>
            ))}
          </div>
        )}
        {(() => {
          // Specials (Season 0) are not counted as a season.
          const regularSeasons = show.seasons.filter((s) => s.number !== 0);
          const seasonCount = regularSeasons.length;
          const episodeCount = regularSeasons.reduce((sum, s) => sum + s.episodes.length, 0);
          return (
            <div className="mt-2 text-xs text-muted-foreground">
              {show.year != null && <>{show.year} &middot; </>}
              {seasonCount} Season{seasonCount !== 1 ? "s" : ""} &middot; {episodeCount} Episodes
            </div>
          );
        })()}
        {show.cast && show.cast.length > 0 && (
          <p className="line-clamp-1 text-xs text-muted-foreground">{show.cast.join(", ")}</p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 md:mt-auto md:pt-3">
          <MediaActionsMenu media={show} mediaType="show" />
          {isSuperuser && <ShowSettingsSheet show={show} />}
        </div>
      </div>
    </div>
  );
}

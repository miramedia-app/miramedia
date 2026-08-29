"use client";

import { MediaDetailHero } from "@/components/media-detail-hero";
import { ShowSettingsSheet } from "@/components/shows/show-settings-sheet";
import type { ShowDetail } from "@/hooks/use-show-detail";

export interface ShowDetailHeroProps {
  show: ShowDetail;
  isSuperuser: boolean;
}

/** Poster + metadata header for the show detail page. */
export function ShowDetailHero({ show, isSuperuser }: ShowDetailHeroProps) {
  // Specials (Season 0) are not counted as a season.
  const regularSeasons = show.seasons.filter((s) => s.number !== 0);
  const seasonCount = regularSeasons.length;
  const episodeCount = regularSeasons.reduce((sum, s) => sum + s.episodes.length, 0);

  return (
    <MediaDetailHero
      media={show}
      mediaType="show"
      metaLine={
        <>
          {show.year != null && <>{show.year} &middot; </>}
          {seasonCount} Season{seasonCount !== 1 ? "s" : ""} &middot; {episodeCount} Episodes
        </>
      }
      settings={isSuperuser ? <ShowSettingsSheet show={show} /> : null}
    />
  );
}

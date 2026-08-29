"use client";

import * as React from "react";
import { MoreHorizontal } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MediaPicture } from "@/components/media-picture";
import { MediaStatusBadge } from "@/components/media-status-badge";
import { MediaActionsMenu } from "@/components/media-actions-menu";
import { AddToWatchlist } from "@/components/watchlists/add-to-watchlist";
import { cn, formatCastLine } from "@/lib/utils";
import type { components } from "@/lib/api/api";

type Media = components["schemas"]["PublicMovie"] | components["schemas"]["PublicShow"];

export interface MediaDetailHeroProps {
  media: Media;
  mediaType: "show" | "movie";
  /** Extra badges rendered next to the status badge. */
  extraBadges?: React.ReactNode;
  /** Year / runtime / season-count line under the genres. */
  metaLine?: React.ReactNode;
  /** Superuser-only settings sheet trigger, rendered inside the actions menu. */
  settings?: React.ReactNode;
  /**
   * Mobile-only primary action (e.g. Play when a file exists). Rendered as the
   * full-width first action; hidden on desktop, which keeps its own layout.
   */
  mobilePrimaryAction?: React.ReactNode;
}

/**
 * Poster + metadata header shared by the show and movie detail pages.
 *
 * Mobile (<sm): compact poster, smaller title, overview clamped with a More/Less
 * toggle, first action full-width and the remaining actions behind a "More" disclosure.
 */
export function MediaDetailHero({
  media,
  mediaType,
  extraBadges,
  metaLine,
  settings,
  mobilePrimaryAction,
}: MediaDetailHeroProps) {
  const [overviewExpanded, setOverviewExpanded] = React.useState(false);
  // Only offer More/Less when the clamped overview actually truncates.
  const overviewRef = React.useRef<HTMLParagraphElement>(null);
  const [overviewClamped, setOverviewClamped] = React.useState(false);
  React.useLayoutEffect(() => {
    const el = overviewRef.current;
    if (!el) return;
    const measure = () => {
      if (!overviewExpanded) setOverviewClamped(el.scrollHeight > el.clientHeight + 1);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [overviewExpanded, media.overview]);
  const [actionsExpanded, setActionsExpanded] = React.useState(false);

  return (
    <div className="flex gap-4 max-sm:flex-col md:items-stretch">
      <div className="w-28 shrink-0 overflow-hidden rounded-xl sm:w-[8.8rem] md:w-44">
        <MediaPicture media={media} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <MediaStatusBadge status={media.status ?? (media.skipped ? "skipped" : "wanted")} />
          {extraBadges}
        </div>
        <h1 className="line-clamp-2 text-xl font-bold tracking-tight sm:line-clamp-1 sm:text-2xl">
          {media.name}
        </h1>
        {media.content_rating && (
          <Badge variant="outline" className="w-fit font-mono text-xs">
            {media.content_rating}
          </Badge>
        )}
        {media.overview && (
          <div className="mt-1">
            <p
              ref={overviewRef}
              className={cn(
                "text-sm leading-relaxed text-muted-foreground",
                overviewExpanded ? "sm:line-clamp-3" : "line-clamp-4 sm:line-clamp-3",
              )}
            >
              {media.overview}
            </p>
            {(overviewClamped || overviewExpanded) && (
              <Button
                type="button"
                variant="link"
                size="xs"
                className="h-auto px-0 text-xs sm:hidden"
                aria-expanded={overviewExpanded}
                onClick={() => setOverviewExpanded((v) => !v)}
              >
                {overviewExpanded ? "Less" : "More"}
              </Button>
            )}
          </div>
        )}
        {media.genres && media.genres.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {media.genres.map((g) => (
              <Badge key={g} variant="secondary" className="text-xs">
                {g}
              </Badge>
            ))}
          </div>
        )}
        {metaLine && <div className="mt-1 text-xs text-muted-foreground">{metaLine}</div>}
        {media.cast && media.cast.length > 0 && (
          <p className="line-clamp-1 text-xs text-muted-foreground">{formatCastLine(media.cast)}</p>
        )}
        <div
          className={cn(
            "mt-3 flex flex-wrap items-center gap-2 md:mt-auto md:pt-3",
            // Mobile: first action full-width, rest in a 2-col grid behind the More toggle.
            "max-sm:[&>div]:grid max-sm:[&>div]:w-full max-sm:[&>div]:grid-cols-2",
            "max-sm:[&>div_button]:w-full max-sm:[&>div>*:first-child]:col-span-full",
            "max-sm:[&>div_a]:w-full max-sm:[&>div>*]:coarse:min-h-11",
            !actionsExpanded && "max-sm:[&>div>*:nth-child(n+2)]:hidden",
          )}
        >
          <MediaActionsMenu
            media={media}
            mediaType={mediaType}
            before={
              mobilePrimaryAction ? <div className="sm:hidden">{mobilePrimaryAction}</div> : null
            }
            afterSubtitles={
              <AddToWatchlist
                mediaKind={mediaType}
                mediaId={media.id ?? ""}
                triggerLabel="Watchlists"
              />
            }
          >
            {settings}
          </MediaActionsMenu>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full sm:hidden"
            aria-expanded={actionsExpanded}
            onClick={() => setActionsExpanded((v) => !v)}
          >
            <MoreHorizontal className="h-4 w-4" />
            {actionsExpanded ? "Fewer actions" : "More actions"}
          </Button>
        </div>
      </div>
    </div>
  );
}

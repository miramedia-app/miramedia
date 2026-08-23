"use client";

import * as React from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { ListTodo, TriangleAlert, EllipsisVertical } from "lucide-react";

import { DirectDownloadAction } from "@/components/direct-download-action";
import { DataListEmpty } from "@/components/data-list";
import { MediaPicture } from "@/components/media-picture";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PlaybackProgressMeter } from "@/components/watchlists/playback-progress-meter";
import { WatchedMenuItems } from "@/components/watchlists/watched-button";
import { WATCH_NEXT_LABEL } from "@/components/watchlists/watchlists-routes";
import { useFeatures, useFeaturesStatus } from "@/components/providers/features-provider";
import { useWatchNext, WATCH_NEXT_MAX_LIMIT, WATCH_NEXT_PAGE_SIZE } from "@/hooks/use-watchlists";
import { importedFileRowActions } from "@/lib/media-download";
import type { UpNextItem } from "@/lib/watchlists";
import {
  asyncListViewState,
  formatCappedItemCount,
  formatListItemCopy,
  upNextPlayLabel,
} from "@/lib/watchlists";

const VideoPlayerDialog = dynamic(
  () => import("@/components/video-player-dialog").then((m) => m.VideoPlayerDialog),
  { ssr: false },
);

export const WATCH_NEXT_OVERVIEW = "Next downloaded episode for each tracked show.";

export function getWatchNextViewState(opts: {
  isPending: boolean;
  isError: boolean;
  count: number;
}) {
  return asyncListViewState({
    isPending: opts.isPending,
    isError: opts.isError,
    isEmpty: opts.count === 0,
  });
}

export function WatchNextDetail() {
  const { watch_next: watchNextEnabled, watch_next_include_specials: includeSpecials } =
    useFeatures();
  const { isError: featuresError } = useFeaturesStatus();
  const [limit, setLimit] = React.useState(WATCH_NEXT_PAGE_SIZE);
  const watchNextQuery = useWatchNext(watchNextEnabled, includeSpecials, limit);
  const items = watchNextQuery.data ?? [];
  const truncated = items.length >= limit;
  const viewState = getWatchNextViewState({
    isPending: watchNextQuery.isPending,
    isError: watchNextQuery.isError,
    count: items.length,
  });

  if (!watchNextEnabled) {
    return (
      <DataListEmpty
        icon={<ListTodo />}
        title={featuresError ? "Features could not be loaded" : `${WATCH_NEXT_LABEL} is disabled`}
        description={
          featuresError
            ? "The feature settings request failed. Check that the backend is reachable."
            : "Enable it in System → Settings → Watchlists."
        }
      />
    );
  }

  return (
    <div className="space-y-8">
      <WatchNextHero
        countLabel={
          viewState === "ready" || viewState === "empty"
            ? formatCappedItemCount(items.length, truncated)
            : null
        }
        coverId={items[0]?.poster_media_id ?? null}
      />

      {viewState === "error" ? (
        <div role="alert">
          <DataListEmpty
            icon={<TriangleAlert />}
            title={`${WATCH_NEXT_LABEL} could not be loaded`}
            description="Check that the backend is reachable, then retry."
            action={
              <Button variant="outline" size="sm" onClick={() => void watchNextQuery.refetch()}>
                Retry
              </Button>
            }
          />
        </div>
      ) : viewState === "pending" ? (
        <div className="space-y-3" aria-busy="true">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-20 rounded-lg bg-muted/30" />
          ))}
        </div>
      ) : viewState === "empty" ? (
        <DataListEmpty
          icon={<ListTodo />}
          title="Nothing queued yet"
          description="Shows with a next episode to watch will appear here."
        />
      ) : (
        <>
          <ul className="divide-y border-y">
            {items.map((item) => (
              <WatchNextRow key={`${item.show_id}-${item.media_id}`} item={item} />
            ))}
          </ul>
          {truncated && limit < WATCH_NEXT_MAX_LIMIT ? (
            <div className="flex justify-center pt-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setLimit((prev) => Math.min(prev + WATCH_NEXT_PAGE_SIZE, WATCH_NEXT_MAX_LIMIT))
                }
              >
                Show more
              </Button>
            </div>
          ) : truncated ? (
            <p className="pt-4 text-center text-sm text-muted-foreground">
              Not all items shown. Mark episodes watched to see more.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

function WatchNextHero({
  countLabel,
  coverId,
}: {
  countLabel: string | null;
  coverId: string | null;
}) {
  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-stretch">
      <div className="w-[8.8rem] shrink-0 overflow-hidden rounded-xl md:w-44">
        {coverId ? (
          <MediaPicture media={{ id: coverId, name: WATCH_NEXT_LABEL, year: null }} priority />
        ) : (
          <div
            className="flex aspect-[2/3] w-full items-center justify-center rounded-xl bg-muted"
            role="img"
            aria-label={`${WATCH_NEXT_LABEL} cover`}
          >
            <ListTodo className="size-12 text-muted-foreground" />
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2">
        <h1 className="line-clamp-2 text-2xl font-bold tracking-tight text-balance">
          {WATCH_NEXT_LABEL}
        </h1>
        <p className="mt-1 line-clamp-3 text-sm leading-relaxed text-pretty text-muted-foreground">
          {WATCH_NEXT_OVERVIEW}
        </p>
        {countLabel != null ? (
          <div className="mt-2 text-xs text-muted-foreground tabular-nums">{countLabel}</div>
        ) : null}
      </div>
    </div>
  );
}

function WatchNextRow({ item }: { item: UpNextItem }) {
  const { streaming, downloads } = useFeatures();
  const { showPlayer, showDownload } = importedFileRowActions({
    streaming,
    downloads,
    imported: Boolean(item.file_id),
  });
  const copy = formatListItemCopy({
    title: item.title,
    showName: item.show_name,
    seasonNumber: item.season_number,
    episodeNumber: item.episode_number,
    episodeTitle: item.episode_title,
    mediaKind: "episode",
  });
  const playLabel = upNextPlayLabel(item.position_ms, item.duration_ms);

  return (
    <li className="flex min-h-11 items-center gap-4 py-3">
      <Link
        href={`/dashboard/shows/${item.show_id}`}
        className="h-14 w-[37px] shrink-0 overflow-hidden rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <MediaPicture media={{ id: item.poster_media_id, name: copy.title, year: null }} />
      </Link>
      <div className="min-w-0 flex-1 space-y-1">
        <Link
          href={`/dashboard/shows/${item.show_id}`}
          className="block truncate text-sm font-medium hover:underline"
        >
          {copy.title}
        </Link>
        {copy.subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{copy.subtitle}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <PlaybackProgressMeter positionMs={item.position_ms} durationMs={item.duration_ms} />
        {showPlayer ? (
          <VideoPlayerDialog
            mediaType="show"
            mediaId={item.media_id}
            fileId={item.file_id}
            title={item.title}
            resumeFromMs={item.position_ms > 0 ? item.position_ms : undefined}
            buttonVariant="ghost"
            buttonSize="icon"
            buttonClassName="min-h-11 min-w-11"
            triggerLabel={playLabel}
          />
        ) : null}
        {showDownload ? (
          <DirectDownloadAction
            mediaType="show"
            mediaId={item.media_id}
            fileId={item.file_id}
            buttonVariant="ghost"
            buttonSize="icon"
            buttonClassName="min-h-11 min-w-11"
          />
        ) : null}
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="min-h-11 min-w-11 text-muted-foreground"
                aria-label="More actions"
              >
                <EllipsisVertical className="size-4" />
              </Button>
            }
          />
          <DropdownMenuContent align="end">
            <WatchedMenuItems mediaKind="episode" mediaId={item.media_id} />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  );
}

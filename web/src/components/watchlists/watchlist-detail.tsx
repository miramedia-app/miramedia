"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import {
  ArrowDown,
  ArrowUp,
  EllipsisVertical,
  ListChecks,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { DataListEmpty } from "@/components/data-list";
import { MediaPicture } from "@/components/media-picture";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DirectDownloadAction } from "@/components/direct-download-action";
import { PlaybackProgressMeter } from "@/components/watchlists/playback-progress-meter";
import { WatchedMenuItems } from "@/components/watchlists/watched-button";
import { WatchlistDetailHero } from "@/components/watchlists/watchlist-detail-hero";
import { WATCHLISTS_BASE } from "@/components/watchlists/watchlists-routes";
import { useFeatures } from "@/components/providers/features-provider";
import {
  useRemoveWatchlistItem,
  useReorderWatchlistItem,
  useWatchlist,
} from "@/hooks/use-watchlists";
import { importedFileRowActions } from "@/lib/media-download";
import type { WatchlistItemView } from "@/lib/watchlists";
import {
  showStatusCopy,
  upNextPlayLabel,
  watchlistDetailViewState,
  watchlistItemCopy,
  watchlistItemHref,
  watchlistItemPlayTarget,
} from "@/lib/watchlists";

const VideoPlayerDialog = dynamic(
  () => import("@/components/video-player-dialog").then((m) => m.VideoPlayerDialog),
  { ssr: false },
);

export function getWatchlistDetailViewState(opts: {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  itemCount: number;
}) {
  return watchlistDetailViewState(opts);
}

export function getMoveControlState(
  index: number,
  total: number,
  direction: "up" | "down",
): { disabled: boolean; label: string } {
  const label = direction === "up" ? "Move up" : "Move down";
  const disabled = direction === "up" ? index <= 0 : index >= total - 1;
  return { disabled, label };
}

export function WatchlistDetail({ watchlistId }: { watchlistId: string }) {
  const detailQuery = useWatchlist(watchlistId);
  const removeItem = useRemoveWatchlistItem();
  const reorderItem = useReorderWatchlistItem();

  const items = detailQuery.data?.items ?? [];
  const viewState = getWatchlistDetailViewState({
    isPending: detailQuery.isPending,
    isError: detailQuery.isError,
    error: detailQuery.error,
    itemCount: items.length,
  });

  if (viewState === "pending") {
    return (
      <div className="space-y-6" aria-busy="true">
        <div className="h-48 rounded-lg bg-muted/30" />
        {Array.from({ length: 3 }).map((_, index) => (
          <div key={index} className="h-20 rounded-lg bg-muted/30" />
        ))}
      </div>
    );
  }

  if (viewState === "not-found") {
    return (
      <DataListEmpty
        icon={<ListChecks />}
        title="Watchlist not found"
        description="This list may have been deleted or you may not have access."
        action={
          <Button variant="outline" size="sm" render={<Link href={WATCHLISTS_BASE} />}>
            Back to Watchlists
          </Button>
        }
      />
    );
  }

  if (viewState === "error") {
    return (
      <div role="alert">
        <DataListEmpty
          icon={<TriangleAlert />}
          title="Watchlist could not be loaded"
          description="Check that the backend is reachable, then retry."
          action={
            <Button variant="outline" size="sm" onClick={() => void detailQuery.refetch()}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  const detail = detailQuery.data!;

  return (
    <div className="space-y-8">
      <WatchlistDetailHero detail={detail} />

      {viewState === "empty" ? (
        <DataListEmpty
          icon={<ListChecks />}
          title="This list is empty"
          description="Add movies, shows, or episodes from their detail pages."
        />
      ) : (
        <ul className="divide-y border-y">
          {items.map((item, index) => (
            <WatchlistItemRow
              key={item.id}
              item={item}
              index={index}
              total={items.length}
              onRemove={() => removeItem.mutate({ watchlistId, itemId: item.id })}
              onMove={(direction) =>
                reorderItem
                  .mutateSerialized({ watchlistId, itemId: item.id, direction })
                  .catch(() => {
                    // Failure UX (toast + optimistic rollback) is handled by the mutation's onError.
                  })
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function WatchlistItemRow({
  item,
  index,
  total,
  onRemove,
  onMove,
}: {
  item: WatchlistItemView;
  index: number;
  total: number;
  onRemove: () => void;
  onMove: (direction: "up" | "down") => void;
}) {
  const { streaming, downloads } = useFeatures();
  const href = watchlistItemHref(item);
  const playTarget = watchlistItemPlayTarget(item);
  const statusCopy = item.media_kind === "show" ? showStatusCopy(item.show_status) : null;
  const durationMs = item.duration_ms ?? item.next_episode?.duration_ms;
  const moveUp = getMoveControlState(index, total, "up");
  const moveDown = getMoveControlState(index, total, "down");

  const copy = watchlistItemCopy(item);
  const { showPlayer, showDownload } = importedFileRowActions({
    streaming,
    downloads,
    imported: playTarget != null,
  });
  const media = (
    <>
      <div className="h-14 w-[37px] shrink-0 overflow-hidden rounded-sm">
        <MediaPicture media={{ id: item.poster_media_id, name: copy.title, year: null }} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="truncate text-sm font-medium">{copy.title}</p>
        {copy.subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{copy.subtitle}</p>
        ) : null}
        {statusCopy ? <p className="text-xs text-muted-foreground">{statusCopy}</p> : null}
      </div>
    </>
  );

  return (
    <li>
      <div className="flex min-h-11 items-center gap-4 py-3">
        {href ? (
          <Link
            href={href}
            className="group flex min-w-0 flex-1 items-center gap-4 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {media}
          </Link>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-4">{media}</div>
        )}
        <div className="flex shrink-0 items-center gap-1">
          {playTarget ? (
            <>
              <PlaybackProgressMeter
                positionMs={playTarget.resumeFromMs ?? 0}
                durationMs={durationMs}
              />
              {showPlayer ? (
                <VideoPlayerDialog
                  mediaType={playTarget.mediaType}
                  mediaId={playTarget.mediaId}
                  fileId={playTarget.fileId}
                  title={playTarget.title}
                  resumeFromMs={
                    playTarget.resumeFromMs && playTarget.resumeFromMs > 0
                      ? playTarget.resumeFromMs
                      : undefined
                  }
                  buttonVariant="ghost"
                  buttonSize="icon"
                  buttonClassName="min-h-11 min-w-11"
                  triggerLabel={upNextPlayLabel(playTarget.resumeFromMs ?? 0, durationMs)}
                />
              ) : null}
              {showDownload ? (
                <DirectDownloadAction
                  mediaType={playTarget.mediaType}
                  mediaId={playTarget.mediaId}
                  fileId={playTarget.fileId}
                  buttonVariant="ghost"
                  buttonSize="icon"
                  buttonClassName="min-h-11 min-w-11"
                />
              ) : null}
            </>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="min-h-11 min-w-11"
            aria-label={moveUp.label}
            disabled={moveUp.disabled}
            onClick={() => onMove("up")}
          >
            <ArrowUp className="size-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="min-h-11 min-w-11"
            aria-label={moveDown.label}
            disabled={moveDown.disabled}
            onClick={() => onMove("down")}
          >
            <ArrowDown className="size-4" />
          </Button>
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
              {playTarget ? (
                <WatchedMenuItems
                  mediaKind={playTarget.watchedMediaKind}
                  mediaId={playTarget.watchedMediaId}
                />
              ) : null}
              <DropdownMenuItem className="text-destructive" onClick={onRemove}>
                <Trash2 className="size-4" />
                Remove from list
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </li>
  );
}

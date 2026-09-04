"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, EllipsisVertical, Play } from "lucide-react";
import { toast } from "sonner";

import { MediaPicture } from "@/components/media-picture";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { VideoPlayerDialog } from "@/components/video-player-dialog";
import { useFeatures } from "@/components/providers/features-provider";
import { getWatchedButtonA11y } from "@/components/watchlists/watched-button";
import { invalidateWatchedItem, setWatchedState } from "@/hooks/use-watched-state";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import {
  continueWatchingCopy,
  continueWatchingQueryEnabled,
  formatProgressPercent,
  watchlistOverflowActionsEnabled,
} from "@/lib/watchlists";

type ContinueWatchingItem = components["schemas"]["ContinueWatchingItem"];

export function ContinueWatchingRow() {
  const {
    continue_watching: continueWatchingEnabled,
    streaming,
    ready: featuresReady,
  } = useFeatures();
  const enabled = continueWatchingQueryEnabled(featuresReady, continueWatchingEnabled) && streaming;
  const continueQuery = useQuery({
    queryKey: ["playback", "continue"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/playback/continue", {
        params: { query: { limit: 12 } },
        signal,
      });
      if (error) throw error;
      return data ?? [];
    },
    enabled,
  });

  if (!enabled || continueQuery.isPending) return null;
  if (continueQuery.isError) {
    return (
      <p role="alert" className="text-sm text-muted-foreground">
        Continue watching could not be loaded.{" "}
        <Button variant="outline" size="sm" onClick={() => void continueQuery.refetch()}>
          Retry
        </Button>
      </p>
    );
  }

  const items = continueQuery.data ?? [];
  if (items.length === 0) return null;

  return (
    <div className="space-y-4">
      <h3 className="text-2xl font-semibold">Continue Watching</h3>
      <div className="flex w-full snap-x snap-mandatory [scrollbar-width:none] gap-3 overflow-x-auto overscroll-x-contain pb-2 sm:grid sm:grid-cols-3 sm:gap-4 sm:overflow-visible sm:pb-0 md:grid-cols-4 lg:grid-cols-5 [&>*]:w-[42vw] [&>*]:shrink-0 [&>*]:snap-start sm:[&>*]:w-auto">
        {items.map((item) => (
          <ContinueWatchingCard key={item.file_id} item={item} />
        ))}
      </div>
    </div>
  );
}

function ContinueWatchingCard({ item }: { item: ContinueWatchingItem }) {
  const progressPct = formatProgressPercent(item.position_ms, item.duration_ms);
  const copy = continueWatchingCopy(item);
  const playTitle = copy.subtitle ? `${copy.title} · ${copy.subtitle}` : copy.title;

  return (
    <div className="min-w-0 space-y-2">
      <VideoPlayerDialog
        mediaType={item.media_kind === "movie" ? "movie" : "show"}
        mediaId={item.media_id}
        fileId={item.file_id}
        title={playTitle}
        resumeFromMs={item.position_ms}
        trigger={
          <button
            type="button"
            className="group w-full min-w-0 space-y-2 rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Play ${playTitle}`}
          >
            <span className="relative block aspect-[2/3] w-full overflow-hidden rounded-lg">
              <MediaPicture
                media={{ id: item.poster_media_id, name: copy.title, year: item.year ?? null }}
              />
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/25 transition-colors group-hover:bg-black/40">
                <span className="flex size-12 items-center justify-center rounded-full bg-black/65 text-white shadow-sm">
                  <Play className="size-6 fill-current" />
                </span>
              </span>
            </span>
            {progressPct != null ? (
              <span className="block h-1 overflow-hidden rounded-full bg-muted">
                <span
                  className="block h-1 rounded-full bg-foreground"
                  style={{ width: `${progressPct}%` }}
                />
              </span>
            ) : null}
          </button>
        }
      />
      <div className="flex min-w-0 items-start gap-1">
        <div className="min-w-0 flex-1 space-y-0.5">
          <p className="truncate text-xs whitespace-nowrap sm:text-sm">{copy.title}</p>
          {copy.subtitle ? (
            <p className="truncate text-xs text-muted-foreground">{copy.subtitle}</p>
          ) : null}
        </div>
        <WatchedMenu item={item} />
      </div>
    </div>
  );
}

const CONTINUE_KEY = ["playback", "continue"] as const;

function WatchedMenu({ item }: { item: ContinueWatchingItem }) {
  const { watchlists, custom_lists } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });
  const queryClient = useQueryClient();

  const mutation = useMutation({
    // Clear the resume point first so the item leaves Continue Watching and a later
    // replay starts fresh, then sync the manual watched flag for watchlists.
    mutationFn: async (watched: boolean) => {
      const { error } = await apiClient.DELETE("/api/v1/playback/progress", {
        params: { query: { file_id: item.file_id } },
      });
      if (error) throw error;
      await setWatchedState({
        media_kind: item.media_kind,
        media_id: item.media_id,
        watched,
      });
    },
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: CONTINUE_KEY });
      const previous = queryClient.getQueryData<ContinueWatchingItem[]>(CONTINUE_KEY);
      queryClient.setQueryData<ContinueWatchingItem[]>(CONTINUE_KEY, (prev) =>
        prev ? prev.filter((entry) => entry.file_id !== item.file_id) : prev,
      );
      return { previous };
    },
    onError: (_error, _watched, context) => {
      if (context) queryClient.setQueryData(CONTINUE_KEY, context.previous);
      toast.error("Failed to update watched status");
    },
    onSuccess: (_data, watched) => {
      toast.success(watched ? "Marked as watched" : "Marked as unwatched");
    },
    onSettled: async () => {
      await invalidateWatchedItem(queryClient, item.media_kind, item.media_id);
      await queryClient.invalidateQueries({ queryKey: ["playback", "progress"] });
    },
  });

  if (!markWatched) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 text-muted-foreground"
            aria-label="More actions"
            disabled={mutation.isPending}
          >
            <EllipsisVertical className="size-4" />
          </Button>
        }
      />
      <DropdownMenuContent align="end">
        <DropdownMenuItem disabled={mutation.isPending} onClick={() => mutation.mutate(true)}>
          <Eye className="size-4" />
          {getWatchedButtonA11y(false).label}
        </DropdownMenuItem>
        <DropdownMenuItem disabled={mutation.isPending} onClick={() => mutation.mutate(false)}>
          <EyeOff className="size-4" />
          {getWatchedButtonA11y(true).label}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

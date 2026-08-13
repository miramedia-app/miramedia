"use client";

import { useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";

import { MediaPicture } from "@/components/media-picture";
import { Button } from "@/components/ui/button";
import { VideoPlayerDialog } from "@/components/video-player-dialog";
import { useFeatures } from "@/components/providers/features-provider";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import {
  continueWatchingCopy,
  continueWatchingQueryEnabled,
  formatProgressPercent,
} from "@/lib/watchlists";

type ContinueWatchingItem = components["schemas"]["ContinueWatchingItem"];

export function ContinueWatchingRow() {
  const { continue_watching: continueWatchingEnabled, ready: featuresReady } = useFeatures();
  const enabled = continueWatchingQueryEnabled(featuresReady, continueWatchingEnabled);
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
      <div className="grid w-full gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
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
    <div className="min-w-0">
      <VideoPlayerDialog
        mediaType={item.media_kind === "movie" ? "movie" : "show"}
        mediaId={item.media_id}
        fileId={item.file_id}
        title={playTitle}
        resumeFromMs={item.position_ms}
        trigger={
          <button
            type="button"
            className="group w-full min-w-0 space-y-2 overflow-hidden rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
              <span className="block h-1 rounded bg-muted">
                <span
                  className="block h-1 rounded bg-foreground"
                  style={{ width: `${progressPct}%` }}
                />
              </span>
            ) : null}
            <span className="block min-w-0 space-y-0.5">
              <span className="block truncate text-sm whitespace-nowrap">{copy.title}</span>
              {copy.subtitle ? (
                <span className="block truncate text-xs text-muted-foreground">
                  {copy.subtitle}
                </span>
              ) : null}
            </span>
          </button>
        }
      />
    </div>
  );
}

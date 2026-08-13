"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type MediaKind = components["schemas"]["MediaKind"];
type WatchState = components["schemas"]["WatchState"];
type WatchStateUpdate = components["schemas"]["WatchStateUpdate"];
type SeasonWatchStateUpdate = components["schemas"]["SeasonWatchStateUpdate"];
type ShowWatchStateUpdate = components["schemas"]["ShowWatchStateUpdate"];

export const WATCHED_CACHE_KEYS = [
  ["playback", "watched"],
  ["playback", "watch-next"],
  ["playback", "continue"],
  ["watchlists"],
] as const;

export function watchedQueryKey(mediaKind: MediaKind, mediaId: string) {
  return ["playback", "watched", mediaKind, mediaId] as const;
}

export async function invalidateWatchedCaches(queryClient: QueryClient) {
  await Promise.all(
    WATCHED_CACHE_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
  );
}

export function applyOptimisticWatched(
  previous: WatchState | undefined,
  watched: boolean,
  variables: Pick<WatchStateUpdate, "media_kind" | "media_id">,
): WatchState {
  return {
    media_kind: variables.media_kind,
    media_id: variables.media_id,
    watched,
    source: "manual",
    watched_at: watched ? (previous?.watched_at ?? new Date().toISOString()) : null,
  };
}

export function showUnwatchedNeedsConfirmation(watched: boolean, affectedEpisodeCount: number) {
  return watched === false && affectedEpisodeCount > 1;
}

export async function setWatchedState(body: WatchStateUpdate): Promise<WatchState> {
  const { data, error } = await apiClient.PUT("/api/v1/playback/watched", { body });
  if (error) throw error;
  return data!;
}

export async function setSeasonWatched(
  body: Omit<SeasonWatchStateUpdate, "include_specials">,
): Promise<void> {
  const { error } = await apiClient.PUT("/api/v1/playback/watched/season", {
    body: { ...body, include_specials: false },
  });
  if (error) throw error;
}

export async function setShowWatched(
  body: Omit<ShowWatchStateUpdate, "include_specials">,
): Promise<void> {
  const { error } = await apiClient.PUT("/api/v1/playback/watched/show", {
    body: { ...body, include_specials: false },
  });
  if (error) throw error;
}

export async function clearViewingActivity(): Promise<void> {
  const { error } = await apiClient.DELETE("/api/v1/playback/viewing-state");
  if (error) throw error;
}

export function buildSetWatchedMutationOptions(queryClient: QueryClient) {
  return {
    mutationFn: setWatchedState,
    onMutate: async (variables: WatchStateUpdate) => {
      const key = watchedQueryKey(variables.media_kind, variables.media_id);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<WatchState>(key);
      queryClient.setQueryData(key, applyOptimisticWatched(previous, variables.watched, variables));
      return { previous, key };
    },
    onError: (
      _error: unknown,
      _variables: WatchStateUpdate,
      context: { previous?: WatchState; key: ReturnType<typeof watchedQueryKey> } | undefined,
    ) => {
      if (context) {
        queryClient.setQueryData(context.key, context.previous);
      }
      toast.error("Failed to update watched status");
    },
    onSuccess: (_data: WatchState, variables: WatchStateUpdate) => {
      toast.success(variables.watched ? "Marked as watched" : "Marked as unwatched");
    },
    onSettled: async () => {
      await invalidateWatchedCaches(queryClient);
    },
  };
}

export function useWatchedState(mediaKind: MediaKind, mediaId: string, enabled = true) {
  return useQuery({
    queryKey: watchedQueryKey(mediaKind, mediaId),
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/playback/watched", {
        params: { query: { media_kind: mediaKind, media_id: mediaId } },
        signal,
      });
      if (error) throw error;
      return data!;
    },
    enabled: enabled && !!mediaId,
    staleTime: 30_000,
  });
}

export function useSetWatched() {
  const queryClient = useQueryClient();
  return useMutation(buildSetWatchedMutationOptions(queryClient));
}

export function useSetSeasonWatched() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: setSeasonWatched,
    onSuccess: (_data, variables) => {
      toast.success(variables.watched ? "Season marked as watched" : "Season marked as unwatched");
    },
    onError: () => {
      toast.error("Failed to update season watched status");
    },
    onSettled: async () => {
      await invalidateWatchedCaches(queryClient);
    },
  });
}

export function useSetShowWatched() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: setShowWatched,
    onSuccess: (_data, variables) => {
      toast.success(variables.watched ? "Show marked as watched" : "Show marked as unwatched");
    },
    onError: () => {
      toast.error("Failed to update show watched status");
    },
    onSettled: async () => {
      await invalidateWatchedCaches(queryClient);
    },
  });
}

export function useClearViewingActivity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: clearViewingActivity,
    onSuccess: async () => {
      toast.success("Viewing activity cleared");
      await invalidateWatchedCaches(queryClient);
      await queryClient.invalidateQueries({ queryKey: ["playback", "progress"] });
    },
    onError: () => {
      toast.error("Failed to clear viewing activity");
    },
  });
}

export function countDownloadedEpisodes(
  seasons: { episodes: { downloaded?: boolean | null }[] }[],
) {
  return seasons.reduce(
    (sum, season) => sum + season.episodes.filter((episode) => episode.downloaded).length,
    0,
  );
}

"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import {
  addToWatchlistToast,
  reorderWatchlistItems,
  removeWatchlistItemOptimistic,
  watchlistItemIds,
} from "@/lib/watchlists";

type WatchlistCreate = components["schemas"]["WatchlistCreate"];
type WatchlistDetail = components["schemas"]["WatchlistDetail"];
type WatchlistItemCreate = components["schemas"]["WatchlistItemCreate"];
type WatchlistItemView = components["schemas"]["WatchlistItemView"];
type WatchlistSummary = components["schemas"]["WatchlistSummary"];
type WatchlistUpdate = components["schemas"]["WatchlistUpdate"];
type UpNextItem = components["schemas"]["UpNextItem"];

export const EMPTY_WATCHLISTS: WatchlistSummary[] = [];

export const watchlistKeys = {
  all: ["watchlists"] as const,
  detail: (id: string) => ["watchlists", id] as const,
};

export const watchNextQueryKey = ["playback", "watch-next"] as const;
export const WATCH_NEXT_PAGE_SIZE = 50;
export const WATCH_NEXT_MAX_LIMIT = 200;

export async function fetchWatchlists(signal?: AbortSignal): Promise<WatchlistSummary[]> {
  const { data, error } = await apiClient.GET("/api/v1/watchlists", { signal });
  if (error) throw error;
  return data ?? [];
}

export async function fetchWatchlistDetail(
  watchlistId: string,
  signal?: AbortSignal,
): Promise<WatchlistDetail> {
  const { data, error } = await apiClient.GET("/api/v1/watchlists/{watchlist_id}", {
    params: { path: { watchlist_id: watchlistId } },
    signal,
  });
  if (error) throw error;
  return data!;
}

export async function fetchWatchNext(
  signal?: AbortSignal,
  includeSpecials?: boolean,
  limit = WATCH_NEXT_PAGE_SIZE,
): Promise<UpNextItem[]> {
  const { data, error } = await apiClient.GET("/api/v1/playback/watch-next", {
    params: {
      query: {
        limit,
        ...(includeSpecials != null ? { include_specials: includeSpecials } : {}),
      },
    },
    signal,
  });
  if (error) throw error;
  return data ?? [];
}

export async function createWatchlist(body: WatchlistCreate): Promise<WatchlistDetail> {
  const { data, error } = await apiClient.POST("/api/v1/watchlists", { body });
  if (error) throw error;
  return data!;
}

export async function updateWatchlist(
  watchlistId: string,
  body: WatchlistUpdate,
): Promise<WatchlistDetail> {
  const { data, error } = await apiClient.PATCH("/api/v1/watchlists/{watchlist_id}", {
    params: { path: { watchlist_id: watchlistId } },
    body,
  });
  if (error) throw error;
  return data!;
}

export async function deleteWatchlist(watchlistId: string): Promise<void> {
  const { error } = await apiClient.DELETE("/api/v1/watchlists/{watchlist_id}", {
    params: { path: { watchlist_id: watchlistId } },
  });
  if (error) throw error;
}

export async function addWatchlistItem(
  watchlistId: string,
  body: WatchlistItemCreate,
): Promise<{ item: WatchlistItemView; created: boolean }> {
  const result = await apiClient.POST("/api/v1/watchlists/{watchlist_id}/items", {
    params: { path: { watchlist_id: watchlistId } },
    body,
  });
  if (result.error) throw result.error;
  const created = result.response?.status === 201;
  return { item: result.data!, created };
}

export async function removeWatchlistItem(watchlistId: string, itemId: string): Promise<void> {
  const { error } = await apiClient.DELETE("/api/v1/watchlists/{watchlist_id}/items/{item_id}", {
    params: { path: { watchlist_id: watchlistId, item_id: itemId } },
  });
  if (error) throw error;
}

export async function reorderWatchlist(
  watchlistId: string,
  itemIds: string[],
): Promise<WatchlistDetail> {
  const { data, error } = await apiClient.PUT("/api/v1/watchlists/{watchlist_id}/items/order", {
    params: { path: { watchlist_id: watchlistId } },
    body: { item_ids: itemIds },
  });
  if (error) throw error;
  return data!;
}

async function invalidateWatchlistQueries(queryClient: QueryClient, watchlistId?: string) {
  await queryClient.invalidateQueries({ queryKey: watchlistKeys.all });
  if (watchlistId) {
    await queryClient.invalidateQueries({ queryKey: watchlistKeys.detail(watchlistId) });
  }
}

export function useWatchlists(enabled = true) {
  return useQuery({
    queryKey: watchlistKeys.all,
    queryFn: ({ signal }) => fetchWatchlists(signal),
    enabled,
    staleTime: 30_000,
  });
}

export function useWatchlist(watchlistId: string, enabled = true) {
  return useQuery({
    queryKey: watchlistKeys.detail(watchlistId),
    queryFn: ({ signal }) => fetchWatchlistDetail(watchlistId, signal),
    enabled: enabled && !!watchlistId,
    staleTime: 15_000,
  });
}

export function useWatchNext(
  enabled = true,
  includeSpecials?: boolean,
  limit = WATCH_NEXT_PAGE_SIZE,
) {
  return useQuery({
    queryKey: [...watchNextQueryKey, includeSpecials ?? "default", limit] as const,
    queryFn: ({ signal }) => fetchWatchNext(signal, includeSpecials, limit),
    enabled,
    staleTime: 30_000,
  });
}

export function useCreateWatchlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createWatchlist,
    onSuccess: async (detail) => {
      toast.success("Watchlist created");
      await invalidateWatchlistQueries(queryClient, detail.id);
    },
    onError: () => {
      toast.error("Failed to create watchlist");
    },
  });
}

export function useUpdateWatchlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ watchlistId, body }: { watchlistId: string; body: WatchlistUpdate }) =>
      updateWatchlist(watchlistId, body),
    onSuccess: async (detail) => {
      toast.success("Watchlist updated");
      queryClient.setQueryData(watchlistKeys.detail(detail.id), detail);
      await invalidateWatchlistQueries(queryClient, detail.id);
    },
    onError: () => {
      toast.error("Failed to update watchlist");
    },
  });
}

export function useDeleteWatchlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteWatchlist,
    onSuccess: async (_data, watchlistId) => {
      toast.success("Watchlist deleted");
      queryClient.removeQueries({ queryKey: watchlistKeys.detail(watchlistId) });
      await invalidateWatchlistQueries(queryClient);
    },
    onError: () => {
      toast.error("Failed to delete watchlist");
    },
  });
}

export function useAddToWatchlist() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ watchlistId, body }: { watchlistId: string; body: WatchlistItemCreate }) =>
      addWatchlistItem(watchlistId, body),
    onSuccess: async (result, variables) => {
      const toastResult = addToWatchlistToast(result.created);
      toast.success(toastResult.message);
      await invalidateWatchlistQueries(queryClient, variables.watchlistId);
      await queryClient.invalidateQueries({ queryKey: watchNextQueryKey });
    },
    onError: () => {
      toast.error("Failed to add to watchlist");
    },
  });
}

export function buildRemoveWatchlistItemMutationOptions(queryClient: QueryClient) {
  return {
    mutationFn: ({ watchlistId, itemId }: { watchlistId: string; itemId: string }) =>
      removeWatchlistItem(watchlistId, itemId),
    onMutate: async ({ watchlistId, itemId }: { watchlistId: string; itemId: string }) => {
      const key = watchlistKeys.detail(watchlistId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<WatchlistDetail>(key);
      if (previous) {
        queryClient.setQueryData<WatchlistDetail>(key, {
          ...previous,
          items: removeWatchlistItemOptimistic(previous.items, itemId),
        });
      }
      return { previous, key };
    },
    onError: (
      _error: unknown,
      _variables: { watchlistId: string; itemId: string },
      context:
        | { previous?: WatchlistDetail; key: ReturnType<typeof watchlistKeys.detail> }
        | undefined,
    ) => {
      if (context?.previous) {
        queryClient.setQueryData(context.key, context.previous);
      }
      toast.error("Failed to remove item");
    },
    onSettled: async (
      _data: void | undefined,
      _error: unknown,
      variables: { watchlistId: string; itemId: string },
    ) => {
      await invalidateWatchlistQueries(queryClient, variables.watchlistId);
      await queryClient.invalidateQueries({ queryKey: watchNextQueryKey });
    },
  };
}

export function useRemoveWatchlistItem() {
  const queryClient = useQueryClient();
  return useMutation(buildRemoveWatchlistItemMutationOptions(queryClient));
}

type ReorderMutationVariables = { watchlistId: string; items: WatchlistItemView[] };

export function buildReorderWatchlistItemsMutationOptions(queryClient: QueryClient) {
  return {
    mutationFn: ({ watchlistId, items }: ReorderMutationVariables) =>
      reorderWatchlist(watchlistId, watchlistItemIds(items)),
    onMutate: async ({ watchlistId, items }: ReorderMutationVariables) => {
      const key = watchlistKeys.detail(watchlistId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<WatchlistDetail>(key);
      if (previous) {
        queryClient.setQueryData<WatchlistDetail>(key, { ...previous, items });
      }
      return { previous, key };
    },
    onError: (
      _error: unknown,
      _variables: ReorderMutationVariables,
      context:
        | { previous?: WatchlistDetail; key: ReturnType<typeof watchlistKeys.detail> }
        | undefined,
    ) => {
      if (context?.previous) {
        queryClient.setQueryData(context.key, context.previous);
      }
      toast.error("Failed to reorder watchlist");
    },
    onSuccess: (detail: WatchlistDetail) => {
      queryClient.setQueryData(watchlistKeys.detail(detail.id), detail);
    },
    onSettled: async (
      _data: WatchlistDetail | undefined,
      _error: unknown,
      variables: ReorderMutationVariables,
    ) => {
      await invalidateWatchlistQueries(queryClient, variables.watchlistId);
    },
  };
}

export async function computeReorderVariables(
  queryClient: QueryClient,
  watchlistId: string,
  itemId: string,
  direction: "up" | "down",
): Promise<ReorderMutationVariables | null> {
  const cached = queryClient.getQueryData<WatchlistDetail>(watchlistKeys.detail(watchlistId));
  const baseItems = cached?.items ?? (await fetchWatchlistDetail(watchlistId)).items;
  const reordered = reorderWatchlistItems(baseItems, itemId, direction);
  if (!reordered) return null;
  return { watchlistId, items: reordered };
}

export function useReorderWatchlistItem() {
  const queryClient = useQueryClient();
  const reorderChainRef = React.useRef<Promise<unknown>>(Promise.resolve());
  const mutation = useMutation(buildReorderWatchlistItemsMutationOptions(queryClient));

  const mutateSerialized = React.useCallback(
    (variables: { watchlistId: string; itemId: string; direction: "up" | "down" }) => {
      const run = reorderChainRef.current.then(async () => {
        const computed = await computeReorderVariables(
          queryClient,
          variables.watchlistId,
          variables.itemId,
          variables.direction,
        );
        if (!computed) return undefined; // boundary move or vanished item: no request
        return mutation.mutateAsync(computed);
      });
      // Keep the serialization chain alive across failures, but hand the
      // UNCAUGHT promise back so callers observe rejections.
      reorderChainRef.current = run.catch(() => undefined);
      return run;
    },
    [mutation, queryClient],
  );

  return { ...mutation, mutateSerialized };
}

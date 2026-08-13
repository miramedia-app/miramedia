import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  put: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    DELETE: mocks.delete,
    GET: mocks.get,
    PUT: mocks.put,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    error: mocks.error,
    success: mocks.success,
  },
}));

import {
  WATCHED_CACHE_KEYS,
  applyOptimisticWatched,
  buildSetWatchedMutationOptions,
  clearViewingActivity,
  invalidateWatchedCaches,
  setSeasonWatched,
  setShowWatched,
  setWatchedState,
  showUnwatchedNeedsConfirmation,
  watchedQueryKey,
} from "@/hooks/use-watched-state";
import type { components } from "@/lib/api/api";

type WatchState = components["schemas"]["WatchState"];

function baseState(overrides: Partial<WatchState> = {}): WatchState {
  return {
    media_kind: "movie",
    media_id: "movie-1",
    watched: false,
    source: null,
    watched_at: null,
    ...overrides,
  };
}

function createClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("watchedQueryKey", () => {
  it("scopes watched queries by media kind and id", () => {
    expect(watchedQueryKey("episode", "ep-1")).toEqual(["playback", "watched", "episode", "ep-1"]);
  });
});

describe("applyOptimisticWatched", () => {
  it("toggles watched while preserving identifiers", () => {
    const next = applyOptimisticWatched(baseState(), true, {
      media_kind: "movie",
      media_id: "movie-1",
    });
    expect(next).toMatchObject({
      media_kind: "movie",
      media_id: "movie-1",
      watched: true,
      source: "manual",
    });
  });
});

describe("buildSetWatchedMutationOptions", () => {
  const body = { media_kind: "movie" as const, media_id: "movie-1", watched: true };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("optimistically toggles watched before the API responds", async () => {
    const queryClient = createClient();
    const key = watchedQueryKey("movie", "movie-1");
    queryClient.setQueryData(key, baseState());

    const options = buildSetWatchedMutationOptions(queryClient);
    const context = await options.onMutate!(body);

    expect(queryClient.getQueryData<WatchState>(key)?.watched).toBe(true);
    expect(context).toEqual({ previous: baseState(), key });
  });

  it("rolls back on API error and shows an error toast", async () => {
    const queryClient = createClient();
    const key = watchedQueryKey("movie", "movie-1");
    const previous = baseState();
    queryClient.setQueryData(key, { ...previous, watched: true });

    const options = buildSetWatchedMutationOptions(queryClient);
    options.onError!(new Error("boom"), body, { previous, key });

    expect(queryClient.getQueryData<WatchState>(key)).toEqual(previous);
    expect(mocks.error).toHaveBeenCalledWith("Failed to update watched status");
  });

  it("shows success toast and invalidates watched-related caches on settle", async () => {
    const queryClient = createClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const options = buildSetWatchedMutationOptions(queryClient);
    options.onSuccess!(baseState({ watched: true }), body);
    await options.onSettled!();

    expect(mocks.success).toHaveBeenCalledWith("Marked as watched");
    for (const queryKey of WATCHED_CACHE_KEYS) {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey });
    }
  });
});

describe("invalidateWatchedCaches", () => {
  it("invalidates watched, watch-next, continue, and watchlists", async () => {
    const queryClient = createClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await invalidateWatchedCaches(queryClient);

    expect(invalidateSpy).toHaveBeenCalledTimes(WATCHED_CACHE_KEYS.length);
    expect(invalidateSpy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([...WATCHED_CACHE_KEYS]);
  });
});

describe("setWatchedState", () => {
  beforeEach(() => {
    mocks.put.mockReset();
  });

  it("PUTs watch state updates", async () => {
    const state = baseState({ watched: true });
    mocks.put.mockResolvedValueOnce({ data: state, error: undefined });

    await expect(
      setWatchedState({ media_kind: "movie", media_id: "movie-1", watched: true }),
    ).resolves.toEqual(state);
    expect(mocks.put).toHaveBeenCalledWith("/api/v1/playback/watched", {
      body: { media_kind: "movie", media_id: "movie-1", watched: true },
    });
  });

  it("throws when the API returns an error", async () => {
    mocks.put.mockResolvedValueOnce({ data: undefined, error: { message: "nope" } });
    await expect(
      setWatchedState({ media_kind: "movie", media_id: "movie-1", watched: false }),
    ).rejects.toEqual({ message: "nope" });
  });
});

describe("batch watched APIs", () => {
  beforeEach(() => {
    mocks.put.mockReset();
  });

  it("marks a season watched", async () => {
    mocks.put.mockResolvedValueOnce({ error: undefined });
    await setSeasonWatched({ show_id: "show-1", season_number: 2, watched: true });
    expect(mocks.put).toHaveBeenCalledWith("/api/v1/playback/watched/season", {
      body: { show_id: "show-1", season_number: 2, watched: true, include_specials: false },
    });
  });

  it("marks a show unwatched", async () => {
    mocks.put.mockResolvedValueOnce({ error: undefined });
    await setShowWatched({ show_id: "show-1", watched: false });
    expect(mocks.put).toHaveBeenCalledWith("/api/v1/playback/watched/show", {
      body: { show_id: "show-1", watched: false, include_specials: false },
    });
  });
});

describe("showUnwatchedNeedsConfirmation", () => {
  it("requires confirmation only when multiple episodes would be affected", () => {
    expect(showUnwatchedNeedsConfirmation(true, 5)).toBe(false);
    expect(showUnwatchedNeedsConfirmation(false, 1)).toBe(false);
    expect(showUnwatchedNeedsConfirmation(false, 2)).toBe(true);
  });
});

describe("clearViewingActivity", () => {
  beforeEach(() => {
    mocks.delete.mockReset();
  });

  it("DELETEs viewing state", async () => {
    mocks.delete.mockResolvedValueOnce({ error: undefined });
    await clearViewingActivity();
    expect(mocks.delete).toHaveBeenCalledWith("/api/v1/playback/viewing-state");
  });
});

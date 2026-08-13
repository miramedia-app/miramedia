import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
    POST: mocks.post,
    PATCH: mocks.patch,
    PUT: mocks.put,
    DELETE: mocks.delete,
  },
}));

vi.mock("sonner", () => ({
  toast: {
    error: mocks.error,
    success: mocks.success,
  },
}));

import {
  buildRemoveWatchlistItemMutationOptions,
  buildReorderWatchlistItemsMutationOptions,
  computeReorderVariables,
  fetchWatchNext,
  fetchWatchlistDetail,
  fetchWatchlists,
  watchlistKeys,
  addWatchlistItem,
} from "@/hooks/use-watchlists";
import type { components } from "@/lib/api/api";
import { watchlistItemIds } from "@/lib/watchlists";

type WatchlistDetail = components["schemas"]["WatchlistDetail"];
type WatchlistItemView = components["schemas"]["WatchlistItemView"];

function detail(id: string, items: WatchlistItemView[]): WatchlistDetail {
  return {
    id,
    name: "List",
    description: null,
    items,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function row(id: string, position: number): WatchlistItemView {
  return {
    id,
    position,
    media_kind: "movie",
    media_id: `media-${id}`,
    title: id,
    poster_media_id: `poster-${id}`,
    watched: false,
  };
}

function createClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("watchlistKeys", () => {
  it("scopes list and detail queries", () => {
    expect(watchlistKeys.all).toEqual(["watchlists"]);
    expect(watchlistKeys.detail("abc")).toEqual(["watchlists", "abc"]);
  });
});

describe("fetchWatchlists", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("loads summaries from the API", async () => {
    mocks.get.mockResolvedValueOnce({ data: [{ id: "1", name: "A" }], error: undefined });
    await expect(fetchWatchlists()).resolves.toEqual([{ id: "1", name: "A" }]);
    expect(mocks.get).toHaveBeenCalledWith("/api/v1/watchlists", { signal: undefined });
  });
});

describe("fetchWatchNext", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("loads watch-next rows", async () => {
    mocks.get.mockResolvedValueOnce({ data: [], error: undefined });
    await expect(fetchWatchNext()).resolves.toEqual([]);
    expect(mocks.get).toHaveBeenCalledWith("/api/v1/playback/watch-next", {
      params: { query: { limit: 50 } },
      signal: undefined,
    });
  });
});

describe("addWatchlistItem", () => {
  beforeEach(() => {
    mocks.post.mockReset();
  });

  it("reports created vs already-present from status code", async () => {
    mocks.post.mockResolvedValueOnce({
      data: row("a", 0),
      error: undefined,
      response: { status: 201 },
    });
    await expect(
      addWatchlistItem("list-1", { media_kind: "movie", media_id: "movie-1" }),
    ).resolves.toEqual({ item: row("a", 0), created: true });

    mocks.post.mockResolvedValueOnce({
      data: row("a", 0),
      error: undefined,
      response: { status: 200 },
    });
    await expect(
      addWatchlistItem("list-1", { media_kind: "movie", media_id: "movie-1" }),
    ).resolves.toEqual({ item: row("a", 0), created: false });
  });
});

describe("buildRemoveWatchlistItemMutationOptions", () => {
  const watchlistId = "list-1";
  const items = [row("a", 0), row("b", 1)];

  it("optimistically removes an item and rolls back on error", async () => {
    const client = createClient();
    client.setQueryData(watchlistKeys.detail(watchlistId), detail(watchlistId, items));
    const options = buildRemoveWatchlistItemMutationOptions(client);

    const context = await options.onMutate?.({ watchlistId, itemId: "a" });
    expect(
      client.getQueryData<WatchlistDetail>(watchlistKeys.detail(watchlistId))?.items,
    ).toHaveLength(1);

    options.onError?.(new Error("boom"), { watchlistId, itemId: "a" }, context);
    expect(
      client.getQueryData<WatchlistDetail>(watchlistKeys.detail(watchlistId))?.items,
    ).toHaveLength(2);
  });
});

describe("buildReorderWatchlistItemsMutationOptions", () => {
  const watchlistId = "list-1";
  const items = [row("a", 0), row("b", 1), row("c", 2)];

  beforeEach(() => {
    mocks.put.mockReset();
    mocks.get.mockReset();
  });

  it("persists the optimistic order instead of applying the move twice", async () => {
    const twoItems = [row("a", 0), row("b", 1)];
    mocks.put.mockResolvedValueOnce({
      data: detail(watchlistId, [row("b", 0), row("a", 1)]),
      error: undefined,
    });
    const client = createClient();
    client.setQueryData(watchlistKeys.detail(watchlistId), detail(watchlistId, twoItems));
    const options = buildReorderWatchlistItemsMutationOptions(client);

    const computed = await computeReorderVariables(client, watchlistId, "a", "down");
    expect(watchlistItemIds(computed!.items)).toEqual(["b", "a"]);
    await options.onMutate?.(computed!);
    await options.mutationFn(computed!);

    expect(mocks.put).toHaveBeenCalledWith(
      "/api/v1/watchlists/{watchlist_id}/items/order",
      expect.objectContaining({
        body: { item_ids: ["b", "a"] },
      }),
    );
  });

  it("optimistically reorders and rolls back on error", async () => {
    const client = createClient();
    client.setQueryData(watchlistKeys.detail(watchlistId), detail(watchlistId, items));
    const options = buildReorderWatchlistItemsMutationOptions(client);

    const computed = await computeReorderVariables(client, watchlistId, "b", "up");
    const context = await options.onMutate?.(computed!);
    const optimistic = client.getQueryData<WatchlistDetail>(watchlistKeys.detail(watchlistId));
    expect(watchlistItemIds(optimistic!.items)).toEqual(["b", "a", "c"]);

    options.onError?.(new Error("boom"), computed!, context);
    expect(
      watchlistItemIds(
        client.getQueryData<WatchlistDetail>(watchlistKeys.detail(watchlistId))!.items,
      ),
    ).toEqual(["a", "b", "c"]);
  });

  it("boundary move sends nothing", async () => {
    const client = createClient();
    client.setQueryData(watchlistKeys.detail(watchlistId), detail(watchlistId, items));

    await expect(computeReorderVariables(client, watchlistId, "a", "up")).resolves.toBeNull();
    expect(mocks.put).not.toHaveBeenCalled();
  });

  it("mutationFn is cache-independent", async () => {
    mocks.put.mockResolvedValueOnce({
      data: detail(watchlistId, [row("b", 0), row("a", 1)]),
      error: undefined,
    });
    const client = createClient();
    client.setQueryData(
      watchlistKeys.detail(watchlistId),
      detail(watchlistId, [row("c", 0), row("b", 1), row("a", 2)]),
    );
    const options = buildReorderWatchlistItemsMutationOptions(client);

    await options.mutationFn({ watchlistId, items: [row("b", 0), row("a", 1)] });

    expect(mocks.put).toHaveBeenCalledWith(
      "/api/v1/watchlists/{watchlist_id}/items/order",
      expect.objectContaining({
        body: { item_ids: ["b", "a"] },
      }),
    );
  });

  it("cache miss falls back to fetch", async () => {
    mocks.get.mockResolvedValueOnce({
      data: detail(watchlistId, [row("a", 0), row("b", 1)]),
      error: undefined,
    });
    const client = createClient();

    const computed = await computeReorderVariables(client, watchlistId, "a", "down");
    expect(watchlistItemIds(computed!.items)).toEqual(["b", "a"]);
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("failure propagates", async () => {
    mocks.put.mockResolvedValueOnce({
      data: undefined,
      error: { detail: "boom" },
    });
    const client = createClient();
    const options = buildReorderWatchlistItemsMutationOptions(client);

    await expect(
      options.mutationFn({ watchlistId, items: [row("b", 0), row("a", 1)] }),
    ).rejects.toBeTruthy();
  });
});

describe("fetchWatchlistDetail", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("loads a watchlist by id", async () => {
    mocks.get.mockResolvedValueOnce({
      data: detail("list-1", []),
      error: undefined,
    });
    await expect(fetchWatchlistDetail("list-1")).resolves.toEqual(detail("list-1", []));
    expect(mocks.get).toHaveBeenCalledWith("/api/v1/watchlists/{watchlist_id}", {
      params: { path: { watchlist_id: "list-1" } },
      signal: undefined,
    });
  });
});

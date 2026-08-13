import { describe, expect, it } from "vitest";

import {
  buildHubCards,
  filterAndSortHubCards,
  filterWatchlistSummaries,
  getMyListsViewState,
  HUB_DEFAULT_SORT,
  upcomingCardMatchesSearch,
  watchNextCardMatchesSearch,
} from "@/components/watchlists/watchlists-hub";
import { getWatchNextViewState } from "@/components/watchlists/watch-next-detail";
import { canSaveWatchlistSettings } from "@/components/watchlists/watchlist-settings-sheet";
import { UPCOMING_LABEL, WATCH_NEXT_LABEL } from "@/components/watchlists/watchlists-routes";
import { watchlistListHref, type WatchlistSummary } from "@/lib/watchlists";

function summary(
  partial: Partial<WatchlistSummary> & Pick<WatchlistSummary, "id" | "name">,
): WatchlistSummary {
  return {
    description: null,
    item_count: 0,
    cover_poster_media_id: null,
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
    ...partial,
  };
}

describe("hub list search", () => {
  it("filters user lists by name and description", () => {
    const lists = [
      summary({ id: "1", name: "Weekend", description: "Friday night" }),
      summary({ id: "2", name: "Kids", description: null }),
    ];
    expect(filterWatchlistSummaries(lists, "week")).toEqual([lists[0]]);
    expect(filterWatchlistSummaries(lists, "friday")).toEqual([lists[0]]);
    expect(filterWatchlistSummaries(lists, "kids")).toEqual([lists[1]]);
  });

  it("keeps pinned cards visible unless search excludes them", () => {
    expect(watchNextCardMatchesSearch("")).toBe(true);
    expect(watchNextCardMatchesSearch("watch")).toBe(true);
    expect(watchNextCardMatchesSearch("weekend")).toBe(false);
    expect(upcomingCardMatchesSearch("")).toBe(true);
    expect(upcomingCardMatchesSearch("upcom")).toBe(true);
    expect(upcomingCardMatchesSearch("weekend")).toBe(false);
  });
});

describe("hub filter and sort", () => {
  const lists = [
    summary({
      id: "1",
      name: "Zebra",
      item_count: 2,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-10T00:00:00Z",
    }),
    summary({
      id: "2",
      name: "Alpha",
      item_count: 0,
      created_at: "2026-08-05T00:00:00Z",
      updated_at: "2026-08-06T00:00:00Z",
    }),
  ];

  const cards = buildHubCards({
    lists,
    watchNextCount: 3,
    watchNextCover: null,
    upcomingCount: 0,
    upcomingCover: null,
  });

  it("builds pinned cards then custom lists", () => {
    expect(cards.map((c) => c.id)).toEqual(["watch-next", "upcoming", "1", "2"]);
    expect(cards[0]?.name).toBe(WATCH_NEXT_LABEL);
    expect(cards[1]?.name).toBe(UPCOMING_LABEL);
  });

  it("filters by search across pinned and custom cards", () => {
    const visible = filterAndSortHubCards(cards, "alpha", [], HUB_DEFAULT_SORT);
    expect(visible.map((c) => c.id)).toEqual(["2"]);
  });

  it("filters by type facet", () => {
    const customOnly = filterAndSortHubCards(
      cards,
      "",
      [{ facetId: "type", operator: "includes", values: ["custom"] }],
      HUB_DEFAULT_SORT,
    );
    expect(customOnly.map((c) => c.id)).toEqual(["2", "1"]);

    const builtIn = filterAndSortHubCards(
      cards,
      "",
      [{ facetId: "type", operator: "includes", values: ["built-in"] }],
      HUB_DEFAULT_SORT,
    );
    expect(builtIn.map((c) => c.id)).toEqual(["watch-next", "upcoming"]);
  });

  it("filters by items facet", () => {
    const empty = filterAndSortHubCards(
      cards,
      "",
      [{ facetId: "items", operator: "includes", values: ["empty"] }],
      HUB_DEFAULT_SORT,
    );
    expect(empty.map((c) => c.id)).toEqual(["upcoming", "2"]);
  });

  it("keeps built-ins pinned while sorting custom lists", () => {
    const byName = filterAndSortHubCards(cards, "", [], "name-asc");
    expect(byName.map((c) => c.id)).toEqual(["watch-next", "upcoming", "2", "1"]);

    const byItems = filterAndSortHubCards(cards, "", [], "items-desc");
    expect(byItems.map((c) => c.id)).toEqual(["watch-next", "upcoming", "1", "2"]);
  });

  it("omits disabled built-in cards", () => {
    const withoutBuiltIns = buildHubCards({
      lists,
      watchNextCount: 3,
      watchNextCover: null,
      upcomingCount: 0,
      upcomingCover: null,
      includeWatchNext: false,
      includeUpcoming: false,
    });
    expect(withoutBuiltIns.map((c) => c.id)).toEqual(["1", "2"]);
  });

  it("propagates truncated onto watch-next and upcoming cards", () => {
    const truncatedCards = buildHubCards({
      lists: [],
      watchNextCount: 50,
      watchNextCover: null,
      upcomingCount: 20,
      upcomingCover: null,
      watchNextTruncated: true,
      upcomingTruncated: true,
    });
    expect(truncatedCards[0]).toMatchObject({ id: "watch-next", truncated: true });
    expect(truncatedCards[1]).toMatchObject({ id: "upcoming", truncated: true });
  });
});

describe("hub and Watch Next view states", () => {
  it("maps My Lists and Watch Next query states", () => {
    expect(getMyListsViewState({ isPending: true, isError: false, count: 0 })).toBe("pending");
    expect(getMyListsViewState({ isPending: false, isError: false, count: 0 })).toBe("empty");
    expect(getMyListsViewState({ isPending: false, isError: true, count: 0 })).toBe("error");
    expect(getMyListsViewState({ isPending: false, isError: false, count: 2 })).toBe("ready");
    expect(getWatchNextViewState({ isPending: true, isError: false, count: 0 })).toBe("pending");
    expect(getWatchNextViewState({ isPending: false, isError: false, count: 0 })).toBe("empty");
  });

  it("validates names and builds list hrefs", () => {
    expect(canSaveWatchlistSettings("A")).toBe(true);
    expect(watchlistListHref("abc")).toContain("abc");
  });
});

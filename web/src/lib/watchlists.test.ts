import { describe, expect, it } from "vitest";

import type { WatchlistItemView } from "@/lib/watchlists";
import {
  addToWatchlistToast,
  asyncListViewState,
  continueWatchingCopy,
  continueWatchingQueryEnabled,
  formatCappedItemCount,
  formatListItemCopy,
  formatPlaybackClock,
  formatPlaybackProgressMeter,
  formatProgressPercent,
  formatSeasonEpisode,
  isHttpNotFound,
  reorderWatchlistItems,
  removeWatchlistItemOptimistic,
  showStatusCopy,
  upNextPlayLabel,
  watchlistDetailViewState,
  watchlistItemCopy,
  watchlistItemIds,
  watchlistItemPlayTarget,
  watchlistOverflowActionsEnabled,
} from "@/lib/watchlists";

function item(
  id: string,
  position: number,
  overrides: Partial<WatchlistItemView> = {},
): WatchlistItemView {
  return {
    id,
    position,
    media_kind: "movie",
    media_id: `media-${id}`,
    title: `Title ${id}`,
    poster_media_id: `poster-${id}`,
    watched: false,
    ...overrides,
  };
}

describe("formatSeasonEpisode", () => {
  it("zero-pads season and episode", () => {
    expect(formatSeasonEpisode(1, 1)).toBe("S01E01");
    expect(formatSeasonEpisode(12, 3)).toBe("S12E03");
  });
});

describe("formatCappedItemCount", () => {
  it("uses singular item only for an untruncated count of one", () => {
    expect(formatCappedItemCount(0, false)).toBe("0 items");
    expect(formatCappedItemCount(1, false)).toBe("1 item");
    expect(formatCappedItemCount(2, false)).toBe("2 items");
  });

  it("appends a plus when the count is truncated", () => {
    expect(formatCappedItemCount(50, true)).toBe("50+ items");
  });
});

describe("formatListItemCopy", () => {
  it("splits a composite episode label into show name and SxxExx · title", () => {
    expect(
      formatListItemCopy({
        title: "Lioness - S03E08 - The Unravelling",
        seasonNumber: 3,
        episodeNumber: 8,
      }),
    ).toEqual({ title: "Lioness", subtitle: "S03E08 · The Unravelling" });
  });

  it("keeps show name as the heading when next-episode fields are provided", () => {
    expect(
      formatListItemCopy({
        title: "House of the Dragon",
        showName: "House of the Dragon",
        seasonNumber: 3,
        episodeNumber: 8,
        episodeTitle: "TBA",
      }),
    ).toEqual({ title: "House of the Dragon", subtitle: "S03E08 · TBA" });
  });

  it("does not repeat a movie title as a subtitle", () => {
    expect(formatListItemCopy({ title: "Alien: Romulus", mediaKind: "movie" })).toEqual({
      title: "Alien: Romulus",
      subtitle: null,
    });
    expect(
      formatListItemCopy({
        title: "Alien: Romulus",
        showName: "Alien: Romulus",
        mediaKind: "movie",
      }),
    ).toEqual({ title: "Alien: Romulus", subtitle: null });
  });

  it("uses the movie year as the subtitle", () => {
    expect(formatListItemCopy({ title: "Alien: Romulus", mediaKind: "movie", year: 2024 })).toEqual(
      { title: "Alien: Romulus", subtitle: "2024" },
    );
  });

  it("uses show_name for upcoming episodes instead of the composite title", () => {
    expect(
      formatListItemCopy({
        title: "Star Trek: Strange New Worlds - S04E05 - Level-Five Transporter Accident",
        showName: "Star Trek: Strange New Worlds",
        seasonNumber: 4,
        episodeNumber: 5,
      }),
    ).toEqual({
      title: "Star Trek: Strange New Worlds",
      subtitle: "S04E05 · Level-Five Transporter Accident",
    });
  });

  it("omits a missing episode title instead of repeating the code", () => {
    expect(
      formatListItemCopy({
        title: "Lioness - S03E08",
        seasonNumber: 3,
        episodeNumber: 8,
      }),
    ).toEqual({ title: "Lioness", subtitle: "S03E08" });
  });
});

describe("continueWatchingCopy", () => {
  it("uses movie name and year", () => {
    expect(
      continueWatchingCopy({
        file_id: "f1",
        media_kind: "movie",
        media_id: "m1",
        title: "Alien: Romulus",
        poster_media_id: "p1",
        position_ms: 1_000,
        duration_ms: 100_000,
        updated_at: "2026-08-13T00:00:00Z",
        year: 2024,
      }),
    ).toEqual({ title: "Alien: Romulus", subtitle: "2024" });
  });

  it("uses show name and SxxExx without the episode title", () => {
    expect(
      continueWatchingCopy({
        file_id: "f1",
        media_kind: "episode",
        media_id: "e1",
        show_id: "s1",
        title: "House of the Dragon",
        poster_media_id: "p1",
        position_ms: 1_000,
        duration_ms: 100_000,
        updated_at: "2026-08-13T00:00:00Z",
        season_number: 3,
        episode_number: 8,
      }),
    ).toEqual({ title: "House of the Dragon", subtitle: "S03E08" });
  });
});

describe("watchlistItemCopy", () => {
  it("does not repeat SxxExx on episode rows", () => {
    expect(
      watchlistItemCopy(
        item("ep", 0, {
          media_kind: "episode",
          title: "Lioness - S03E08 - The Unravelling",
          season_number: 3,
          episode_number: 8,
        }),
      ),
    ).toEqual({ title: "Lioness", subtitle: "S03E08 · The Unravelling" });
  });

  it("uses next-episode fields for show rows", () => {
    expect(
      watchlistItemCopy(
        item("show", 0, {
          media_kind: "show",
          title: "House of the Dragon",
          next_episode: {
            file_id: "file-1",
            media_id: "ep-1",
            season_number: 3,
            episode_number: 8,
            episode_title: "TBA",
            title: "House of the Dragon · S03E08",
            watched: false,
            position_ms: 0,
          },
        }),
      ),
    ).toEqual({ title: "House of the Dragon", subtitle: "S03E08 · TBA" });
  });

  it("uses the year as the subtitle on movie rows", () => {
    expect(
      watchlistItemCopy(
        item("m", 0, {
          media_kind: "movie",
          title: "Alien: Romulus",
          year: 2024,
        }),
      ),
    ).toEqual({ title: "Alien: Romulus", subtitle: "2024" });
  });
});

describe("formatProgressPercent", () => {
  it("returns null without duration", () => {
    expect(formatProgressPercent(1000, null)).toBeNull();
    expect(formatProgressPercent(1000, undefined)).toBeNull();
    expect(formatProgressPercent(1000, 0)).toBeNull();
    expect(formatProgressPercent(1000, -1)).toBeNull();
  });

  it("clamps between 0 and 100", () => {
    expect(formatProgressPercent(500, 1000)).toBe(50);
    expect(formatProgressPercent(-100, 1000)).toBe(0);
    expect(formatProgressPercent(2000, 1000)).toBe(100);
  });
});

describe("formatPlaybackClock", () => {
  it("formats minutes and seconds without hours", () => {
    expect(formatPlaybackClock(0)).toBe("0:00");
    expect(formatPlaybackClock(65_000)).toBe("1:05");
  });

  it("includes hours when the duration is at least an hour", () => {
    expect(formatPlaybackClock(3_661_000)).toBe("1:01:01");
  });
});

describe("formatPlaybackProgressMeter", () => {
  it("returns null without duration or meaningful progress", () => {
    expect(formatPlaybackProgressMeter(1_000, null)).toBeNull();
    expect(formatPlaybackProgressMeter(0, 100_000)).toBeNull();
  });

  it("returns elapsed, remaining, and percent", () => {
    expect(formatPlaybackProgressMeter(30_000, 120_000)).toEqual({
      elapsed: "0:30",
      remaining: "1:30",
      duration: "2:00",
      percent: 25,
    });
  });

  it("rounds the displayed percent", () => {
    expect(formatPlaybackProgressMeter(1_000, 3_000)?.percent).toBe(33);
  });
});

describe("upNextPlayLabel", () => {
  it("uses Resume when meaningful progress exists", () => {
    expect(upNextPlayLabel(0, 100_000)).toBe("Play");
    expect(upNextPlayLabel(2000, 100_000)).toBe("Resume");
  });
});

describe("showStatusCopy", () => {
  it("maps show availability states", () => {
    expect(showStatusCopy("all_available_episodes_watched")).toBe("All available episodes watched");
    expect(showStatusCopy("no_downloaded_episode_available")).toBe(
      "No downloaded episode available",
    );
    expect(showStatusCopy(null)).toBeNull();
  });
});

describe("asyncListViewState", () => {
  it("prefers error over pending", () => {
    expect(asyncListViewState({ isPending: true, isError: true, isEmpty: false })).toBe("error");
  });

  it("surfaces empty only after a successful load", () => {
    expect(asyncListViewState({ isPending: false, isError: false, isEmpty: true })).toBe("empty");
    expect(asyncListViewState({ isPending: false, isError: false, isEmpty: false })).toBe("ready");
  });
});

describe("watchlistDetailViewState", () => {
  it("distinguishes not-found from generic errors", () => {
    expect(
      watchlistDetailViewState({
        isPending: false,
        isError: true,
        error: { status: 404 },
        itemCount: 0,
      }),
    ).toBe("not-found");
    expect(
      watchlistDetailViewState({
        isPending: false,
        isError: true,
        error: { status: 500 },
        itemCount: 0,
      }),
    ).toBe("error");
  });

  it("reports empty lists separately from ready", () => {
    expect(
      watchlistDetailViewState({
        isPending: false,
        isError: false,
        error: null,
        itemCount: 0,
      }),
    ).toBe("empty");
    expect(
      watchlistDetailViewState({
        isPending: false,
        isError: false,
        error: null,
        itemCount: 2,
      }),
    ).toBe("ready");
  });
});

describe("isHttpNotFound", () => {
  it("detects 404-shaped errors", () => {
    expect(isHttpNotFound({ status: 404 })).toBe(true);
    expect(isHttpNotFound({ statusCode: 404 })).toBe(true);
    expect(isHttpNotFound({ status: 500 })).toBe(false);
  });
});

describe("reorderWatchlistItems", () => {
  const items = [item("a", 0), item("b", 1), item("c", 2)];

  it("moves an item up and reindexes positions", () => {
    const next = reorderWatchlistItems(items, "b", "up");
    expect(next?.map((row) => row.id)).toEqual(["b", "a", "c"]);
    expect(next?.map((row) => row.position)).toEqual([0, 1, 2]);
  });

  it("returns null when the move is out of bounds", () => {
    expect(reorderWatchlistItems(items, "a", "up")).toBeNull();
    expect(reorderWatchlistItems(items, "c", "down")).toBeNull();
  });
});

describe("removeWatchlistItemOptimistic", () => {
  it("drops the item and compacts positions", () => {
    const items = [item("a", 0), item("b", 1)];
    const next = removeWatchlistItemOptimistic(items, "a");
    expect(next).toHaveLength(1);
    expect(next[0]?.id).toBe("b");
    expect(next[0]?.position).toBe(0);
  });
});

describe("watchlistItemIds", () => {
  it("preserves list order", () => {
    expect(watchlistItemIds([item("a", 0), item("b", 1)])).toEqual(["a", "b"]);
  });
});

describe("watchlistItemPlayTarget", () => {
  it("resolves movie and episode play targets", () => {
    expect(
      watchlistItemPlayTarget(
        item("m", 0, {
          media_kind: "movie",
          file_id: "file-1",
          position_ms: 1200,
        }),
      ),
    ).toMatchObject({
      fileId: "file-1",
      mediaType: "movie",
      resumeFromMs: 1200,
      watchedMediaKind: "movie",
    });
    expect(
      watchlistItemPlayTarget(
        item("e", 0, {
          media_kind: "episode",
          file_id: "file-2",
          media_id: "ep-1",
        }),
      ),
    ).toMatchObject({
      fileId: "file-2",
      mediaType: "show",
      watchedMediaKind: "episode",
      watchedMediaId: "ep-1",
    });
  });

  it("uses show next episode when present", () => {
    expect(
      watchlistItemPlayTarget(
        item("s", 0, {
          media_kind: "show",
          next_episode: {
            file_id: "file-3",
            media_id: "ep-2",
            season_number: 1,
            episode_number: 2,
            episode_title: "Pilot",
            title: "Show · S01E02",
            watched: false,
            position_ms: 0,
          },
        }),
      ),
    ).toMatchObject({ fileId: "file-3", watchedMediaId: "ep-2" });
  });

  it("returns null when nothing is playable", () => {
    expect(watchlistItemPlayTarget(item("s", 0, { media_kind: "show" }))).toBeNull();
    expect(watchlistItemPlayTarget(item("m", 0, { media_kind: "movie" }))).toBeNull();
  });
});

describe("addToWatchlistToast", () => {
  it("treats duplicates as success", () => {
    expect(addToWatchlistToast(true).message).toBe("Added to watchlist");
    expect(addToWatchlistToast(false).message).toBe("Already in watchlist");
  });
});

describe("watchlistOverflowActionsEnabled", () => {
  it("hides mark watched and add when no provider is enabled", () => {
    expect(watchlistOverflowActionsEnabled({ watchlists: false, custom_lists: true })).toEqual({
      markWatched: false,
      addToWatchlist: false,
    });
    expect(watchlistOverflowActionsEnabled({ watchlists: false, custom_lists: false })).toEqual({
      markWatched: false,
      addToWatchlist: false,
    });
  });

  it("keeps mark watched when custom lists are off", () => {
    expect(watchlistOverflowActionsEnabled({ watchlists: true, custom_lists: false })).toEqual({
      markWatched: true,
      addToWatchlist: false,
    });
  });

  it("enables both when the native provider and custom lists are on", () => {
    expect(watchlistOverflowActionsEnabled({ watchlists: true, custom_lists: true })).toEqual({
      markWatched: true,
      addToWatchlist: true,
    });
  });
});

describe("continueWatchingQueryEnabled", () => {
  it("does not fetch until features have loaded from the server", () => {
    expect(continueWatchingQueryEnabled(false, true)).toBe(false);
    expect(continueWatchingQueryEnabled(false, false)).toBe(false);
    expect(continueWatchingQueryEnabled(false, undefined)).toBe(false);
  });

  it("fetches only when the server flag is on", () => {
    expect(continueWatchingQueryEnabled(true, true)).toBe(true);
    expect(continueWatchingQueryEnabled(true, false)).toBe(false);
    expect(continueWatchingQueryEnabled(true, undefined)).toBe(false);
  });
});

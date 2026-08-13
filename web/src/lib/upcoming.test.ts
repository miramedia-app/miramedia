import { describe, expect, it } from "vitest";
import {
  filterUpcomingItems,
  formatUpcomingDateHeading,
  groupUpcomingByDate,
  parseIsoDate,
  toIsoDate,
  posterMediaForUpcoming,
  upcomingItemCopy,
  upcomingItemHref,
  upcomingSearchMatch,
  formatAirTime,
  type UpcomingItem,
} from "./upcoming";

function item(
  partial: Partial<UpcomingItem> &
    Pick<UpcomingItem, "media_type" | "id" | "title" | "date" | "poster_id">,
): UpcomingItem {
  return {
    downloaded: false,
    show_id: null,
    show_name: null,
    season_number: null,
    episode_number: null,
    ...partial,
  };
}

describe("upcomingItemHref", () => {
  it("links movies to the movie detail page", () => {
    expect(
      upcomingItemHref(
        item({
          media_type: "movie",
          id: "m1",
          title: "Film",
          date: "2026-08-07",
          poster_id: "m1",
        }),
      ),
    ).toBe("/dashboard/movies/m1");
  });

  it("links episodes to the show detail page", () => {
    expect(
      upcomingItemHref(
        item({
          media_type: "episode",
          id: "e1",
          title: "Show · S01E01",
          date: "2026-08-07",
          poster_id: "s1",
          show_id: "s1",
        }),
      ),
    ).toBe("/dashboard/shows/s1");
  });
});

describe("groupUpcomingByDate", () => {
  it("keeps chronological date buckets from shuffled input", () => {
    const grouped = groupUpcomingByDate([
      item({
        media_type: "movie",
        id: "b",
        title: "B",
        date: "2026-08-02",
        poster_id: "b",
      }),
      item({
        media_type: "episode",
        id: "c",
        title: "C",
        date: "2026-08-01",
        poster_id: "c",
        show_id: "s",
      }),
      item({
        media_type: "movie",
        id: "a",
        title: "A",
        date: "2026-08-01",
        poster_id: "a",
      }),
    ]);
    expect(grouped.map((g) => g.date)).toEqual(["2026-08-01", "2026-08-02"]);
    expect(grouped[0]?.items.map((i) => i.id)).toEqual(["c", "a"]);
  });

  it("reverses bucket order for date-desc without reordering within a bucket", () => {
    const grouped = groupUpcomingByDate(
      [
        item({ media_type: "movie", id: "b", title: "B", date: "2026-08-02", poster_id: "b" }),
        item({ media_type: "movie", id: "c", title: "C", date: "2026-08-01", poster_id: "c" }),
        item({ media_type: "movie", id: "a", title: "A", date: "2026-08-01", poster_id: "a" }),
      ],
      "date-desc",
    );
    expect(grouped.map((g) => g.date)).toEqual(["2026-08-02", "2026-08-01"]);
    expect(grouped[1]?.items.map((i) => i.id)).toEqual(["c", "a"]);
  });
});

describe("toIsoDate / parseIsoDate", () => {
  it("round-trips a local calendar date without UTC drift", () => {
    // Jan 1 00:30 local: toISOString() would report 2025-12-31 west of UTC.
    expect(toIsoDate(new Date(2026, 0, 1, 0, 30))).toBe("2026-01-01");
    expect(toIsoDate(parseIsoDate("2026-08-07")!)).toBe("2026-08-07");
  });

  it("zero-pads month and day", () => {
    expect(toIsoDate(new Date(2026, 2, 9, 12))).toBe("2026-03-09");
  });

  it("returns null for malformed input", () => {
    expect(parseIsoDate("not-a-date")).toBeNull();
  });
});

describe("formatUpcomingDateHeading", () => {
  it("formats YYYY-MM-DD as a local calendar date", () => {
    expect(formatUpcomingDateHeading("2026-08-07")).toMatch(/Aug/);
    expect(formatUpcomingDateHeading("2026-08-07")).toMatch(/7/);
  });
});

describe("upcomingItemCopy", () => {
  it("uses show name as the heading and SxxExx · episode title as the subtitle", () => {
    expect(
      upcomingItemCopy(
        item({
          media_type: "episode",
          id: "e1",
          title: "Star Trek: Strange New Worlds - S04E05 - Level-Five Transporter Accident",
          date: "2026-08-07",
          poster_id: "s1",
          show_id: "s1",
          show_name: "Star Trek: Strange New Worlds",
          season_number: 4,
          episode_number: 5,
        }),
      ),
    ).toEqual({
      title: "Star Trek: Strange New Worlds",
      subtitle: "S04E05 · Level-Five Transporter Accident",
    });
  });

  it("appends air time after the episode title", () => {
    expect(
      upcomingItemCopy(
        item({
          media_type: "episode",
          id: "e1",
          title: "Star Trek: Strange New Worlds - S04E05 - Level-Five Transporter Accident",
          date: "2026-08-07",
          poster_id: "s1",
          show_id: "s1",
          show_name: "Star Trek: Strange New Worlds",
          season_number: 4,
          episode_number: 5,
          air_time: "21:00",
        }),
      ),
    ).toEqual({
      title: "Star Trek: Strange New Worlds",
      subtitle: `S04E05 · Level-Five Transporter Accident · ${formatAirTime("21:00")}`,
    });
  });

  it("does not repeat a movie title on a second line", () => {
    expect(
      upcomingItemCopy(
        item({
          media_type: "movie",
          id: "m1",
          title: "Alien: Romulus",
          date: "2024-08-16",
          poster_id: "m1",
        }),
      ),
    ).toEqual({ title: "Alien: Romulus", subtitle: null });
  });
});

describe("posterMediaForUpcoming", () => {
  it("prefers show name for episode posters", () => {
    expect(
      posterMediaForUpcoming(
        item({
          media_type: "episode",
          id: "e1",
          title: "Show · S01E01 · Pilot",
          date: "2026-08-07",
          poster_id: "s1",
          show_id: "s1",
          show_name: "Show",
        }),
      ),
    ).toEqual({ id: "s1", name: "Show", year: null });
  });
});

describe("upcomingSearchMatch", () => {
  const row = item({
    media_type: "episode",
    id: "e1",
    title: "S01E01 · Pilot",
    date: "2026-08-07",
    poster_id: "s1",
    show_name: "Severance",
  });

  it("matches on title and show name, case-insensitively", () => {
    expect(upcomingSearchMatch(row, "pilot")).toBe(true);
    expect(upcomingSearchMatch(row, "SEVER")).toBe(true);
  });

  it("returns true for a blank query and false for a miss", () => {
    expect(upcomingSearchMatch(row, "  ")).toBe(true);
    expect(upcomingSearchMatch(row, "andor")).toBe(false);
  });
});

describe("filterUpcomingItems", () => {
  const ep = item({
    media_type: "episode",
    id: "e1",
    title: "Pilot",
    date: "2026-08-07",
    poster_id: "p1",
    show_name: "Severance",
    downloaded: true,
  });
  const movie = item({
    media_type: "movie",
    id: "m1",
    title: "Dune",
    date: "2026-08-09",
    poster_id: "p2",
  });
  const items = [ep, movie];

  it("filters by free-text search", () => {
    expect(filterUpcomingItems(items, "dune", [])).toEqual([movie]);
  });

  it("filters by the type facet", () => {
    expect(
      filterUpcomingItems(items, "", [
        { facetId: "type", operator: "includes", values: ["movie"] },
      ]),
    ).toEqual([movie]);
  });

  it("filters by the status facet", () => {
    expect(
      filterUpcomingItems(items, "", [
        { facetId: "status", operator: "includes", values: ["downloaded"] },
      ]),
    ).toEqual([ep]);
  });

  it("honors an excludes operator", () => {
    expect(
      filterUpcomingItems(items, "", [
        { facetId: "type", operator: "excludes", values: ["movie"] },
      ]),
    ).toEqual([ep]);
  });

  it("combines search and filters", () => {
    expect(
      filterUpcomingItems(items, "dune", [
        { facetId: "type", operator: "includes", values: ["episode"] },
      ]),
    ).toEqual([]);
  });
});

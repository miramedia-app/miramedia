import { describe, expect, it } from "vitest";

import {
  normalizeWatchlistName,
  validateWatchlistName,
  watchlistSelectLabel,
} from "@/components/watchlists/add-to-watchlist";

describe("normalizeWatchlistName", () => {
  it("trims surrounding whitespace", () => {
    expect(normalizeWatchlistName("  Movies  ")).toBe("Movies");
  });
});

describe("validateWatchlistName", () => {
  it("rejects empty names", () => {
    expect(validateWatchlistName("")).toBe("Name is required");
    expect(validateWatchlistName("   ")).toBe("Name is required");
    expect(validateWatchlistName("Favorites")).toBeNull();
  });
});

describe("watchlistSelectLabel", () => {
  const lists = [
    { id: "1251cbb4-4b31-4ba2-ac4c-b3131de1a956", name: "Weekend" },
    { id: "other", name: "Later" },
  ];

  it("returns the list name for a selected id, not the id", () => {
    expect(watchlistSelectLabel(lists, lists[0]!.id)).toBe("Weekend");
  });

  it("falls back to the placeholder when nothing is selected", () => {
    expect(watchlistSelectLabel(lists, "")).toBe("Select a list");
  });
});

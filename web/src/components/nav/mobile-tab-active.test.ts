import { describe, expect, it } from "vitest";
import { isTabActive, selectMobileTabs } from "./mobile-tab-active";

describe("isTabActive", () => {
  it("home matches only the exact dashboard route", () => {
    expect(isTabActive("/dashboard", "/dashboard")).toBe(true);
    expect(isTabActive("/dashboard/", "/dashboard")).toBe(true);
    expect(isTabActive("/dashboard/shows", "/dashboard")).toBe(false);
  });

  it("section tabs match themselves and nested routes", () => {
    expect(isTabActive("/dashboard/shows", "/dashboard/shows")).toBe(true);
    expect(isTabActive("/dashboard/shows/abc/", "/dashboard/shows")).toBe(true);
    expect(isTabActive("/dashboard/movies/1", "/dashboard/shows")).toBe(false);
    expect(isTabActive("/dashboard/showsx", "/dashboard/shows")).toBe(false);
  });

  it("handles a null pathname", () => {
    expect(isTabActive(null, "/dashboard")).toBe(false);
  });
});

describe("selectMobileTabs", () => {
  it("uses Watchlists as the fourth tab when the feature is enabled", () => {
    expect(selectMobileTabs(true).map((t) => t.title)).toEqual([
      "Home",
      "Shows",
      "Movies",
      "Watchlists",
    ]);
    expect(selectMobileTabs(true)[3]?.url).toBe("/dashboard/watchlists");
  });

  it("falls back to Torrents when watchlists are disabled", () => {
    expect(selectMobileTabs(false).map((t) => t.title)).toEqual([
      "Home",
      "Shows",
      "Movies",
      "Torrents",
    ]);
    expect(selectMobileTabs(false)[3]?.url).toBe("/dashboard/torrents");
  });
});

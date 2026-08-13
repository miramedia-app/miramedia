import { describe, expect, it } from "vitest";
import {
  UPCOMING_BASE,
  UPCOMING_LABEL,
  WATCHLISTS_BASE,
  WATCHLISTS_SIDEBAR,
  WATCH_NEXT_LABEL,
  WATCH_NEXT_PATH,
  isWatchlistsSidebarActive,
  watchlistDetailPath,
} from "./watchlists-routes";

describe("watchlists sidebar", () => {
  it("exposes Watchlists as the label and base URL", () => {
    expect(WATCHLISTS_SIDEBAR).toEqual({ title: "Watchlists", url: WATCHLISTS_BASE });
  });

  it("highlights Watchlists for hub, Watch Next, Upcoming, and detail routes", () => {
    expect(isWatchlistsSidebarActive("/dashboard/watchlists")).toBe(true);
    expect(isWatchlistsSidebarActive(WATCH_NEXT_PATH)).toBe(true);
    expect(isWatchlistsSidebarActive(UPCOMING_BASE)).toBe(true);
    expect(
      isWatchlistsSidebarActive("/dashboard/watchlists/00000000-0000-0000-0000-000000000001"),
    ).toBe(true);
    expect(isWatchlistsSidebarActive("/dashboard/shows")).toBe(false);
  });
});

describe("watchlist paths", () => {
  it("builds Watch Next, Upcoming, and detail URLs", () => {
    expect(WATCH_NEXT_PATH).toBe("/dashboard/watchlists/watch-next");
    expect(WATCH_NEXT_LABEL).toBe("Watch Next");
    expect(UPCOMING_BASE).toBe("/dashboard/watchlists/upcoming");
    expect(UPCOMING_LABEL).toBe("Upcoming");
    expect(watchlistDetailPath("abc")).toBe("/dashboard/watchlists/abc");
  });
});

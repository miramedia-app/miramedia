export const WATCHLISTS_BASE = "/dashboard/watchlists";

export const WATCH_NEXT_PATH = `${WATCHLISTS_BASE}/watch-next`;

export const WATCH_NEXT_LABEL = "Watch Next";

export const UPCOMING_BASE = `${WATCHLISTS_BASE}/upcoming`;

export const UPCOMING_LABEL = "Upcoming";

export const UPCOMING_OVERVIEW = "Upcoming releases for shows and movies you're tracking.";

export const watchlistDetailPath = (watchlistId: string) => `${WATCHLISTS_BASE}/${watchlistId}`;

export const WATCHLISTS_SIDEBAR = {
  title: "Watchlists",
  url: WATCHLISTS_BASE,
} as const;

/** Sidebar highlights Watchlists for hub, Watch Next, Upcoming, and UUID detail. */
export function isWatchlistsSidebarActive(pathname: string): boolean {
  const normalized = pathname.replace(/\/$/, "") || "/";
  return normalized.startsWith(WATCHLISTS_BASE);
}

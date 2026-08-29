/**
 * Whether a bottom-tab route is the active one for `pathname`.
 * `/dashboard` (home) matches only exactly; every other tab matches itself
 * and its nested routes (`/dashboard/shows/123`), but a longer sibling tab
 * always wins so `/dashboard/shows` never lights up for `/dashboard/showsx`.
 */
export function isTabActive(pathname: string | null, url: string, home = "/dashboard"): boolean {
  if (!pathname) return false;
  const path = pathname.replace(/\/+$/, "") || "/";
  if (url === home) return path === home;
  return path === url || path.startsWith(`${url}/`);
}

export type MobileTabSpec = { title: string; url: string };

export const HOME_TAB: MobileTabSpec = { title: "Home", url: "/dashboard" };
export const SHOWS_TAB: MobileTabSpec = { title: "Shows", url: "/dashboard/shows" };
export const MOVIES_TAB: MobileTabSpec = { title: "Movies", url: "/dashboard/movies" };
export const WATCHLISTS_TAB: MobileTabSpec = { title: "Watchlists", url: "/dashboard/watchlists" };
export const TORRENTS_TAB: MobileTabSpec = { title: "Torrents", url: "/dashboard/torrents" };

/** Home / Shows / Movies plus Watchlists when the feature is on, else Torrents. */
export function selectMobileTabs(watchlistsEnabled: boolean): MobileTabSpec[] {
  return [HOME_TAB, SHOWS_TAB, MOVIES_TAB, watchlistsEnabled ? WATCHLISTS_TAB : TORRENTS_TAB];
}

import type { components } from "@/lib/api/api";

export type ContinueWatchingItem = components["schemas"]["ContinueWatchingItem"];
export type UpNextItem = components["schemas"]["UpNextItem"];
export type WatchlistDetail = components["schemas"]["WatchlistDetail"];
export type WatchlistItemView = components["schemas"]["WatchlistItemView"];
export type WatchlistSummary = components["schemas"]["WatchlistSummary"];
export type WatchlistMediaKind = components["schemas"]["WatchlistItemCreate"]["media_kind"];

export type AsyncListViewState = "pending" | "error" | "empty" | "ready";

export type WatchlistDetailViewState = "pending" | "error" | "not-found" | "empty" | "ready";

export type WatchlistPlayTarget = {
  fileId: string;
  mediaId: string;
  mediaType: "movie" | "show";
  title: string;
  resumeFromMs?: number;
  watchedMediaKind: "movie" | "episode";
  watchedMediaId: string;
};

export function formatSeasonEpisode(season: number, episode: number): string {
  return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
}

export function formatCappedItemCount(count: number, truncated: boolean): string {
  const suffix = count === 1 && !truncated ? "item" : "items";
  return truncated ? `${count}+ ${suffix}` : `${count} ${suffix}`;
}

export type ListItemCopy = {
  title: string;
  subtitle: string | null;
};

const COMPOSITE_EPISODE_LABEL_RE = /^(.*?)\s[-–·]\s(S\d{2}E\d{2})(?:\s[-–·]\s(.*))?$/i;

function parseCompositeEpisodeLabel(title: string): {
  showName: string;
  code: string;
  episodeTitle: string | null;
} | null {
  const match = COMPOSITE_EPISODE_LABEL_RE.exec(title);
  if (!match) return null;
  return {
    showName: match[1]!.trim(),
    code: match[2]!.toUpperCase(),
    episodeTitle: match[3]?.trim() || null,
  };
}

function cleanEpisodeTitle(
  episodeTitle: string | null | undefined,
  opts: { heading: string; code: string | null },
): string | null {
  const trimmed = episodeTitle?.trim() || null;
  if (!trimmed) return null;
  if (opts.code && trimmed.toUpperCase() === opts.code) return null;
  if (trimmed === opts.heading) return null;
  return trimmed;
}

export function formatListItemCopy(input: {
  title: string;
  showName?: string | null;
  seasonNumber?: number | null;
  episodeNumber?: number | null;
  episodeTitle?: string | null;
  mediaKind?: "movie" | "show" | "episode";
  year?: number | null;
}): ListItemCopy {
  if (input.mediaKind === "movie") {
    return {
      title: input.title.trim(),
      subtitle: input.year != null ? String(input.year) : null,
    };
  }

  const parsed = parseCompositeEpisodeLabel(input.title);
  const code =
    input.seasonNumber != null && input.episodeNumber != null
      ? formatSeasonEpisode(input.seasonNumber, input.episodeNumber)
      : (parsed?.code ?? null);
  const title = (input.showName?.trim() || parsed?.showName || input.title).trim();
  const episodeTitle = cleanEpisodeTitle(input.episodeTitle ?? parsed?.episodeTitle, {
    heading: title,
    code,
  });
  const parts = [code, episodeTitle].filter((part): part is string => Boolean(part));
  return { title, subtitle: parts.length > 0 ? parts.join(" · ") : null };
}

export function continueWatchingCopy(item: ContinueWatchingItem): ListItemCopy {
  if (item.media_kind === "movie") {
    return formatListItemCopy({ title: item.title, mediaKind: "movie", year: item.year });
  }
  return {
    title: item.title.trim(),
    subtitle:
      item.season_number != null && item.episode_number != null
        ? formatSeasonEpisode(item.season_number, item.episode_number)
        : null,
  };
}

export function watchlistItemCopy(item: WatchlistItemView): ListItemCopy {
  if (item.media_kind === "movie") {
    return formatListItemCopy({ title: item.title, mediaKind: "movie", year: item.year });
  }
  if (item.media_kind === "show") {
    return formatListItemCopy({
      title: item.title,
      mediaKind: "show",
      showName: item.title,
      seasonNumber: item.next_episode?.season_number,
      episodeNumber: item.next_episode?.episode_number,
      episodeTitle: item.next_episode?.episode_title,
    });
  }
  return formatListItemCopy({
    title: item.title,
    mediaKind: "episode",
    seasonNumber: item.season_number,
    episodeNumber: item.episode_number,
  });
}

export function formatProgressPercent(
  positionMs: number,
  durationMs?: number | null,
): number | null {
  if (durationMs == null || durationMs <= 0) return null;
  return Math.min(100, Math.max(0, (positionMs / durationMs) * 100));
}

export function formatPlaybackClock(positionMs: number): string {
  const totalSec = Math.max(0, Math.floor(positionMs / 1000));
  const hours = Math.floor(totalSec / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  const seconds = totalSec % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export type PlaybackProgressMeterCopy = {
  elapsed: string;
  remaining: string;
  duration: string;
  percent: number;
};

export function formatPlaybackProgressMeter(
  positionMs: number,
  durationMs?: number | null,
): PlaybackProgressMeterCopy | null {
  const percent = formatProgressPercent(positionMs, durationMs);
  if (percent == null || percent < 1 || durationMs == null) return null;
  return {
    elapsed: formatPlaybackClock(positionMs),
    remaining: formatPlaybackClock(Math.max(0, durationMs - positionMs)),
    duration: formatPlaybackClock(durationMs),
    percent: Math.round(percent),
  };
}

export function upNextPlayLabel(positionMs: number, durationMs?: number | null): "Play" | "Resume" {
  const pct = formatProgressPercent(positionMs, durationMs);
  return pct != null && pct >= 1 ? "Resume" : "Play";
}

export function showStatusCopy(status: WatchlistItemView["show_status"]): string | null {
  if (status === "all_available_episodes_watched") {
    return "All available episodes watched";
  }
  if (status === "no_downloaded_episode_available") {
    return "No downloaded episode available";
  }
  return null;
}

export function asyncListViewState(opts: {
  isPending: boolean;
  isError: boolean;
  isEmpty: boolean;
}): AsyncListViewState {
  if (opts.isError) return "error";
  if (opts.isPending) return "pending";
  if (opts.isEmpty) return "empty";
  return "ready";
}

export function isHttpNotFound(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  if ("status" in error && (error as { status?: number }).status === 404) return true;
  if ("statusCode" in error && (error as { statusCode?: number }).statusCode === 404) return true;
  return false;
}

export function watchlistDetailViewState(opts: {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  itemCount: number;
}): WatchlistDetailViewState {
  if (opts.isError) {
    return isHttpNotFound(opts.error) ? "not-found" : "error";
  }
  if (opts.isPending) return "pending";
  if (opts.itemCount === 0) return "empty";
  return "ready";
}

export function watchlistItemIds(items: WatchlistItemView[]): string[] {
  return items.map((item) => item.id);
}

export function reorderWatchlistItems(
  items: WatchlistItemView[],
  itemId: string,
  direction: "up" | "down",
): WatchlistItemView[] | null {
  const index = items.findIndex((item) => item.id === itemId);
  if (index < 0) return null;
  const swapIndex = direction === "up" ? index - 1 : index + 1;
  if (swapIndex < 0 || swapIndex >= items.length) return null;
  const next = [...items];
  const [moved] = next.splice(index, 1);
  next.splice(swapIndex, 0, moved);
  return next.map((item, position) => ({ ...item, position }));
}

export function removeWatchlistItemOptimistic(
  items: WatchlistItemView[],
  itemId: string,
): WatchlistItemView[] {
  return items
    .filter((item) => item.id !== itemId)
    .map((item, position) => ({ ...item, position }));
}

export function watchlistItemHref(item: WatchlistItemView): string | null {
  if (item.media_kind === "movie") {
    return `/dashboard/movies/${item.media_id}`;
  }
  if (item.media_kind === "show") {
    return `/dashboard/shows/${item.media_id}`;
  }
  if (item.show_id) {
    return `/dashboard/shows/${item.show_id}`;
  }
  return null;
}

export function watchlistItemPlayTarget(item: WatchlistItemView): WatchlistPlayTarget | null {
  if (item.media_kind === "movie") {
    if (!item.file_id) return null;
    return {
      fileId: item.file_id,
      mediaId: item.media_id,
      mediaType: "movie",
      title: item.title,
      resumeFromMs: item.position_ms ?? undefined,
      watchedMediaKind: "movie",
      watchedMediaId: item.media_id,
    };
  }
  if (item.media_kind === "episode") {
    if (!item.file_id) return null;
    return {
      fileId: item.file_id,
      mediaId: item.media_id,
      mediaType: "show",
      title: item.title,
      resumeFromMs: item.position_ms ?? undefined,
      watchedMediaKind: "episode",
      watchedMediaId: item.media_id,
    };
  }
  const next = item.next_episode;
  if (!next?.file_id) return null;
  return {
    fileId: next.file_id,
    mediaId: next.media_id,
    mediaType: "show",
    title: next.title,
    resumeFromMs: next.position_ms ?? undefined,
    watchedMediaKind: "episode",
    watchedMediaId: next.media_id,
  };
}

export function addToWatchlistToast(created: boolean): { type: "success"; message: string } {
  return {
    type: "success",
    message: created ? "Added to watchlist" : "Already in watchlist",
  };
}

/** Overflow-menu visibility. Delete is never gated here. */
export function watchlistOverflowActionsEnabled(features: {
  watchlists: boolean;
  custom_lists: boolean;
}): { markWatched: boolean; addToWatchlist: boolean } {
  return {
    markWatched: features.watchlists,
    addToWatchlist: features.watchlists && features.custom_lists,
  };
}

export function continueWatchingQueryEnabled(
  featuresReady: boolean,
  continueWatching: boolean | undefined,
): boolean {
  return featuresReady && continueWatching === true;
}

export function watchlistListHref(watchlistId: string): string {
  return `/dashboard/watchlists/${watchlistId}`;
}

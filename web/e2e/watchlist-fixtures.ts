import type { Request } from "@playwright/test";

import type { ApiHandler } from "./fixtures";
import {
  authEntryRoutes,
  createAuthSessionState,
  sessionAuthRoutes,
  unauthorizedMe,
} from "./fixtures";

// Stateful watchlist / playback mocks for the private-watchlists browser flow.
// Two isolated users share a fixed media catalog; list and watched state are per user.

export const USER_A_EMAIL = "user-a@example.com";
export const USER_B_EMAIL = "user-b@example.com";
export const FIXTURE_PASSWORD = "fixture-password-not-a-secret";

export const USER_A_ID = "00000000-0000-0000-0000-000000000001";
export const USER_B_ID = "00000000-0000-0000-0000-000000000002";

export const SHOW_ID = "11111111-1111-1111-1111-111111111111";
export const MOVIE_ID = "22222222-2222-2222-2222-222222222222";
export const EP1_ID = "33333333-3333-3333-3333-333333333333";
export const EP2_ID = "44444444-4444-4444-4444-444444444444";
export const FILE_EP1 = "55555555-5555-5555-5555-555555555555";
export const FILE_EP2 = "66666666-6666-6666-6666-666666666666";
export const FILE_MOVIE = "77777777-7777-7777-7777-777777777777";
export const POSTER_SHOW = "88888888-8888-8888-8888-888888888888";
export const POSTER_MOVIE = "99999999-9999-9999-9999-999999999999";
export const SEASON_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01";

export const SHOW_NAME = "Fixture Show";
export const MOVIE_NAME = "Fixture Movie";
const NOW = "2026-08-10T12:00:00Z";

type MediaKind = "movie" | "show" | "episode";
type StoredItem = { id: string; position: number; media_kind: MediaKind; media_id: string };

type WatchlistRecord = {
  id: string;
  name: string;
  description: string | null;
  items: StoredItem[];
  created_at: string;
  updated_at: string;
};

type WatchedRecord = {
  watched: boolean;
  source: "manual" | "derived" | null;
  watched_at: string | null;
};

type UserStore = {
  watchlists: Map<string, WatchlistRecord>;
  watched: Map<string, WatchedRecord>;
  trackedShows: Set<string>;
};

export type WatchlistMockState = {
  session: ReturnType<typeof createAuthSessionState>;
  users: Record<string, UserStore>;
};

function watchedKey(mediaKind: MediaKind, mediaId: string) {
  return `${mediaKind}:${mediaId}`;
}

function emptyUserStore(): UserStore {
  return { watchlists: new Map(), watched: new Map(), trackedShows: new Set() };
}

export function createWatchlistMockState(): WatchlistMockState {
  const session = createAuthSessionState(
    [
      { id: USER_A_ID, email: USER_A_EMAIL },
      { id: USER_B_ID, email: USER_B_EMAIL },
    ],
    USER_A_EMAIL,
  );
  session.loggedOut = true;
  return {
    session,
    users: {
      [USER_A_ID]: emptyUserStore(),
      [USER_B_ID]: emptyUserStore(),
    },
  };
}

export function currentStore(state: WatchlistMockState): UserStore {
  return state.users[state.session.current.id]!;
}

function parseJson<T>(req: Request): T {
  return JSON.parse(req.postData() ?? "{}") as T;
}

function nextEpisodeForShow(store: UserStore) {
  for (const episode of showEpisodes()) {
    const key = watchedKey("episode", episode.id);
    const watched = store.watched.get(key)?.watched ?? false;
    if (!watched) return episode;
  }
  return null;
}

function showEpisodes() {
  return [
    {
      id: EP1_ID,
      season_number: 1,
      episode_number: 1,
      episode_title: "Pilot",
      file_id: FILE_EP1,
      duration_ms: 1_800_000,
    },
    {
      id: EP2_ID,
      season_number: 1,
      episode_number: 2,
      episode_title: "Second",
      file_id: FILE_EP2,
      duration_ms: 1_800_000,
    },
  ];
}

function itemView(store: UserStore, item: StoredItem) {
  if (item.media_kind === "movie") {
    const watched = store.watched.get(watchedKey("movie", MOVIE_ID))?.watched ?? false;
    return {
      id: item.id,
      position: item.position,
      media_kind: "movie" as const,
      media_id: MOVIE_ID,
      title: MOVIE_NAME,
      poster_media_id: POSTER_MOVIE,
      watched,
      year: 2024,
      file_id: FILE_MOVIE,
      position_ms: 0,
      duration_ms: 3_600_000,
    };
  }

  const next = nextEpisodeForShow(store);
  const allWatched = next == null;
  return {
    id: item.id,
    position: item.position,
    media_kind: "show" as const,
    media_id: SHOW_ID,
    title: SHOW_NAME,
    poster_media_id: POSTER_SHOW,
    watched: allWatched,
    show_status: allWatched ? ("all_available_episodes_watched" as const) : null,
    next_episode: next
      ? {
          media_id: next.id,
          season_number: next.season_number,
          episode_number: next.episode_number,
          episode_title: next.episode_title,
          file_id: next.file_id,
          duration_ms: next.duration_ms,
        }
      : null,
  };
}

function watchlistDetail(store: UserStore, record: WatchlistRecord) {
  return {
    id: record.id,
    name: record.name,
    description: record.description,
    items: [...record.items]
      .sort((a, b) => a.position - b.position)
      .map((item) => itemView(store, item)),
    created_at: record.created_at,
    updated_at: record.updated_at,
  };
}

function watchlistSummaries(store: UserStore) {
  return [...store.watchlists.values()].map((record) => {
    const ordered = [...record.items].sort((a, b) => a.position - b.position);
    const first = ordered[0];
    const cover = first == null ? null : first.media_kind === "movie" ? MOVIE_ID : POSTER_SHOW;
    return {
      id: record.id,
      name: record.name,
      description: record.description,
      item_count: record.items.length,
      cover_poster_media_id: cover,
      created_at: record.created_at,
      updated_at: record.updated_at,
    };
  });
}

function computeUpNext(store: UserStore) {
  const items = [];
  for (const showId of store.trackedShows) {
    if (showId !== SHOW_ID) continue;
    const next = nextEpisodeForShow(store);
    if (!next) continue;
    items.push({
      file_id: next.file_id,
      media_kind: "episode" as const,
      media_id: next.id,
      show_id: SHOW_ID,
      show_name: SHOW_NAME,
      season_number: next.season_number,
      episode_number: next.episode_number,
      episode_title: next.episode_title,
      title: `${SHOW_NAME} ${formatCode(next.season_number, next.episode_number)}`,
      poster_media_id: POSTER_SHOW,
      watched: false,
      position_ms: 0,
      duration_ms: next.duration_ms,
      activity_at: NOW,
    });
  }
  return items;
}

function formatCode(season: number, episode: number) {
  return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
}

function getWatched(store: UserStore, mediaKind: MediaKind, mediaId: string) {
  const record = store.watched.get(watchedKey(mediaKind, mediaId));
  return {
    media_kind: mediaKind,
    media_id: mediaId,
    watched: record?.watched ?? false,
    source: record?.source ?? null,
    watched_at: record?.watched_at ?? null,
  };
}

function setWatched(store: UserStore, mediaKind: MediaKind, mediaId: string, watched: boolean) {
  store.watched.set(watchedKey(mediaKind, mediaId), {
    watched,
    source: "manual",
    watched_at: watched ? NOW : null,
  });
  if (mediaKind === "show") {
    if (watched) {
      for (const episode of showEpisodes()) {
        setWatched(store, "episode", episode.id, true);
      }
    }
    store.trackedShows.add(SHOW_ID);
  }
  if (mediaKind === "episode") {
    store.trackedShows.add(SHOW_ID);
  }
}

function showDetailBundle() {
  return {
    show: {
      id: SHOW_ID,
      name: SHOW_NAME,
      overview: "Fixture overview",
      year: 2024,
      external_id: "tvmaze-fixture",
      metadata_provider: "tvmaze",
      ended: false,
      skipped: false,
      status: "downloaded",
      library: "Default",
      wanted_episode_count: 0,
      downloaded_episode_count: 2,
      seasons: [
        {
          id: SEASON_ID,
          number: 1,
          downloaded: true,
          skipped: false,
          status: "downloaded",
          episodes: showEpisodes().map((episode) => ({
            id: episode.id,
            number: episode.episode_number,
            title: episode.episode_title,
            overview: "",
            air_date: "2026-08-01",
            downloaded: true,
            skipped: false,
            status: "downloaded",
          })),
        },
      ],
    },
    torrents: [],
    subtitles_by_episode: {},
  };
}

function seasonFiles() {
  return showEpisodes().map((episode) => ({
    id: episode.file_id,
    episode_id: episode.id,
    quality: 2,
    torrent_id: null,
    codec: "h264",
    hdr: false,
    source: "",
    variant: "",
    extra: "",
    import_status: "imported",
    attempt_count: 0,
    imported: true,
    status: "downloaded",
    file_status: "imported",
    file_name: `${formatCode(1, episode.episode_number)}.mp4`,
  }));
}

function movieDetailBundle() {
  return {
    movie: {
      id: MOVIE_ID,
      name: MOVIE_NAME,
      overview: "Fixture overview",
      year: 2024,
      external_id: "tmdb-fixture",
      metadata_provider: "tmdb",
      skipped: false,
      library: "Default",
      downloaded: true,
      status: "downloaded",
      torrents: [],
    },
    files: [
      {
        id: FILE_MOVIE,
        movie_id: MOVIE_ID,
        quality: 2,
        codec: "h264",
        hdr: false,
        source: "",
        variant: "",
        extra: "",
        import_status: "imported",
        attempt_count: 0,
        imported: true,
        status: "downloaded",
        file_status: "imported",
        file_name: "fixture-movie.mp4",
      },
    ],
    subtitles: [],
  };
}

function mediaShellRoutes(): Record<string, ApiHandler> {
  return {
    "GET /api/v1/shows/recommended": () => ({ body: [] }),
    "GET /api/v1/movies/recommended": () => ({ body: [] }),
    "GET /api/v1/system/settings": () => ({
      body: { indexers: { quality_options: [], codec_options: [] } },
    }),
    "GET /api/v1/movies/libraries": () => ({ body: [] }),
    "GET /api/v1/static/image/*": () => ({ status: 404, body: { detail: "no poster" } }),
    [`GET /api/v1/shows/${SHOW_ID}/detail-bundle`]: () => ({ body: showDetailBundle() }),
    [`GET /api/v1/shows/${SHOW_ID}/torrents`]: () => ({ body: [] }),
    [`GET /api/v1/seasons/${SEASON_ID}/files`]: () => ({ body: seasonFiles() }),
    [`GET /api/v1/movies/${MOVIE_ID}/detail-bundle`]: () => ({ body: movieDetailBundle() }),
    [`GET /api/v1/movies/${MOVIE_ID}/torrents`]: () => ({ body: [] }),
    "GET /api/v1/watchlists/upcoming": () => ({
      body: {
        items: [
          {
            media_type: "episode",
            id: EP2_ID,
            title: `${SHOW_NAME} ${formatCode(1, 2)}`,
            date: "2026-08-17",
            air_time: "20:00",
            poster_id: POSTER_SHOW,
            show_id: SHOW_ID,
            show_name: SHOW_NAME,
            season_number: 1,
            episode_number: 2,
            downloaded: true,
          },
        ],
        window_start: "2026-08-01",
        window_end: "2026-08-31",
        truncated: false,
      },
    }),
  };
}

export function watchlistApiRoutes(state: WatchlistMockState): Record<string, ApiHandler> {
  return {
    ...authEntryRoutes({ me: unauthorizedMe }),
    ...sessionAuthRoutes(state.session),
    ...mediaShellRoutes(),
    "GET /api/v1/watchlists": () => ({ body: watchlistSummaries(currentStore(state)) }),
    "POST /api/v1/watchlists": (req) => {
      const store = currentStore(state);
      const body = parseJson<{ name: string; description?: string | null }>(req);
      const id = crypto.randomUUID();
      const record: WatchlistRecord = {
        id,
        name: body.name.trim(),
        description: body.description?.trim() ? body.description.trim() : null,
        items: [],
        created_at: NOW,
        updated_at: NOW,
      };
      store.watchlists.set(id, record);
      return { status: 201, body: watchlistDetail(store, record) };
    },
    "GET /api/v1/watchlists/*": (req) => {
      const store = currentStore(state);
      const pathname = new URL(req.url()).pathname;
      const watchlistId = pathname.split("/").pop()!;
      const record = store.watchlists.get(watchlistId);
      if (!record) return { status: 404, body: { detail: "Not found" } };
      return { body: watchlistDetail(store, record) };
    },
    "PATCH /api/v1/watchlists/*": (req) => {
      const store = currentStore(state);
      const pathname = new URL(req.url()).pathname;
      const watchlistId = pathname.split("/").pop()!;
      const record = store.watchlists.get(watchlistId);
      if (!record) return { status: 404, body: { detail: "Not found" } };
      const body = parseJson<{ name?: string; description?: string | null }>(req);
      if (body.name != null) record.name = body.name.trim();
      if (body.description !== undefined) {
        record.description = body.description?.trim() ? body.description.trim() : null;
      }
      record.updated_at = NOW;
      return { body: watchlistDetail(store, record) };
    },
    "DELETE /api/v1/watchlists/*": (req) => {
      const store = currentStore(state);
      const pathname = new URL(req.url()).pathname;
      const itemMatch = pathname.match(/^\/api\/v1\/watchlists\/([^/]+)\/items\/([^/]+)$/);
      if (itemMatch) {
        const [, watchlistId, itemId] = itemMatch;
        const record = store.watchlists.get(watchlistId!);
        if (!record) return { status: 404, body: { detail: "Not found" } };
        const nextItems = record.items
          .filter((item) => item.id !== itemId)
          .map((item, position) => ({ ...item, position }));
        if (nextItems.length === record.items.length) {
          return { status: 404, body: { detail: "Not found" } };
        }
        record.items = nextItems;
        record.updated_at = NOW;
        return { status: 204 };
      }
      const watchlistId = pathname.split("/").pop()!;
      if (!store.watchlists.delete(watchlistId)) {
        return { status: 404, body: { detail: "Not found" } };
      }
      return { status: 204 };
    },
    "POST /api/v1/watchlists/*": (req) => {
      const store = currentStore(state);
      const pathname = new URL(req.url()).pathname;
      const match = pathname.match(/^\/api\/v1\/watchlists\/([^/]+)\/items$/);
      if (!match) return { status: 404, body: { detail: "Not found" } };
      const watchlistId = match[1]!;
      const record = store.watchlists.get(watchlistId);
      if (!record) return { status: 404, body: { detail: "Not found" } };
      const body = parseJson<{ media_kind: MediaKind; media_id: string }>(req);
      const duplicate = record.items.find(
        (item) => item.media_kind === body.media_kind && item.media_id === body.media_id,
      );
      if (duplicate) {
        return { status: 200, body: itemView(store, duplicate) };
      }
      const item: StoredItem = {
        id: crypto.randomUUID(),
        position: record.items.length,
        media_kind: body.media_kind,
        media_id: body.media_id,
      };
      record.items.push(item);
      record.updated_at = NOW;
      if (body.media_kind === "show" && body.media_id === SHOW_ID) {
        store.trackedShows.add(SHOW_ID);
      }
      return { status: 201, body: itemView(store, item) };
    },
    "PUT /api/v1/watchlists/*": (req) => {
      const store = currentStore(state);
      const pathname = new URL(req.url()).pathname;
      const match = pathname.match(/^\/api\/v1\/watchlists\/([^/]+)\/items\/order$/);
      if (!match) return { status: 404, body: { detail: "Not found" } };
      const watchlistId = match[1]!;
      const record = store.watchlists.get(watchlistId);
      if (!record) return { status: 404, body: { detail: "Not found" } };
      const body = parseJson<{ item_ids: string[] }>(req);
      const byId = new Map(record.items.map((item) => [item.id, item]));
      const reordered = body.item_ids
        .map((id, position) => {
          const item = byId.get(id);
          return item ? { ...item, position } : null;
        })
        .filter((item): item is StoredItem => item != null);
      if (reordered.length !== record.items.length) {
        return { status: 422, body: { detail: "Invalid order" } };
      }
      record.items = reordered;
      record.updated_at = NOW;
      return { body: watchlistDetail(store, record) };
    },
    "GET /api/v1/playback/watch-next": () => ({ body: computeUpNext(currentStore(state)) }),
    "GET /api/v1/playback/watched": (req) => {
      const store = currentStore(state);
      const url = new URL(req.url());
      const mediaKind = url.searchParams.get("media_kind") as MediaKind;
      const mediaId = url.searchParams.get("media_id") ?? "";
      return { body: getWatched(store, mediaKind, mediaId) };
    },
    "PUT /api/v1/playback/watched": (req) => {
      const store = currentStore(state);
      const body = parseJson<{ media_kind: MediaKind; media_id: string; watched: boolean }>(req);
      setWatched(store, body.media_kind, body.media_id, body.watched);
      return { body: getWatched(store, body.media_kind, body.media_id) };
    },
  };
}

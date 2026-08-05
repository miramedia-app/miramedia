/**
 * Pure, injectable orchestration for optional subtitle-track loading in the
 * video player. Kept free of React/DOM globals so it can be unit-tested in Node
 * with deferred fetch promises and fake object-URL methods.
 *
 * Ownership contract: every object URL created here is either transferred to the
 * caller via `install` or revoked before this function resolves. The abort
 * signal plus `isCurrent` form the single ownership token — if the load is no
 * longer current when bodies finish, created URLs are revoked instead of
 * installed, so nothing leaks past a close, media change, or superseding load.
 */

export type SubtitleTrack = { lang: string; url: string };

export interface SubtitleLoadDeps {
  fetch: typeof fetch;
  createObjectURL: (blob: Blob) => string;
  revokeObjectURL: (url: string) => void;
}

/**
 * Fetch every subtitle language concurrently and tolerate per-track failures.
 * Resolves to the tracks that succeeded, each owning a freshly created URL.
 * Never rejects (network/read failures are swallowed per track).
 */
export async function fetchSubtitleTracks(
  languages: string[],
  trackUrl: (lang: string) => string,
  signal: AbortSignal,
  deps: SubtitleLoadDeps,
): Promise<SubtitleTrack[]> {
  const settled = await Promise.allSettled(
    languages.map(async (lang): Promise<SubtitleTrack | null> => {
      const res = await deps.fetch(trackUrl(lang), {
        signal,
        credentials: "include",
      });
      if (!res.ok) return null;
      const text = await res.text();
      const blob = new Blob([text], { type: "text/vtt" });
      return { lang, url: deps.createObjectURL(blob) };
    }),
  );

  const tracks: SubtitleTrack[] = [];
  for (const outcome of settled) {
    if (outcome.status === "fulfilled" && outcome.value) {
      tracks.push(outcome.value);
    }
  }
  return tracks;
}

/**
 * Run subtitle loading concurrently and install the results only if the load is
 * still current when they finish; otherwise revoke every created URL. Safe to
 * fire-and-forget: never rejects.
 */
export async function loadSubtitlesIfCurrent(opts: {
  languages: string[];
  trackUrl: (lang: string) => string;
  signal: AbortSignal;
  deps: SubtitleLoadDeps;
  isCurrent: () => boolean;
  install: (tracks: SubtitleTrack[]) => void;
}): Promise<void> {
  const { signal, deps, isCurrent, install } = opts;
  let tracks: SubtitleTrack[] = [];
  try {
    tracks = await fetchSubtitleTracks(opts.languages, opts.trackUrl, signal, deps);
  } catch {
    // fetchSubtitleTracks tolerates per-track failure; guard defensively so a
    // fire-and-forget caller never sees an unhandled rejection.
    tracks = [];
  }

  if (tracks.length === 0) return;

  if (signal.aborted || !isCurrent()) {
    for (const track of tracks) deps.revokeObjectURL(track.url);
    return;
  }

  install(tracks);
}

type ProbeResponse = {
  direct_play: boolean;
  hls_playlist_url?: string | null;
};

export interface PlaybackLoadEnv {
  fetch: typeof fetch;
  probeUrl: string;
  streamUrl: string;
  apiUrl: string;
  signal: AbortSignal;
  /** Fire optional subtitle loading; must NOT be awaited by playback. */
  startSubtitles: () => void;
  canPlayHlsNatively: () => boolean;
  playWithMediabunny: (source: { type: "url"; url: string }, signal: AbortSignal) => Promise<void>;
  /** Safari warm-cache HLS: play the playlist natively. */
  onNativeHls: (hlsUrl: string) => void;
  /** Fall back to the raw direct stream URL on the native <video>. */
  onDirect: (streamUrl: string) => void;
}

/**
 * Orchestrate a single playback load. Subtitle loading is started up front and
 * intentionally never awaited, so the media probe/playback path begins without
 * waiting for subtitle bodies. Mirrors the previous inline `loadAndPlay`
 * control flow (native HLS, Mediabunny remux, raw direct stream fallback).
 */
export async function runPlaybackLoad(env: PlaybackLoadEnv): Promise<void> {
  const { signal } = env;

  // Start optional subtitles concurrently; do not await them.
  env.startSubtitles();

  try {
    const probeRes = await env.fetch(env.probeUrl, {
      signal,
      credentials: "include",
    });
    if (probeRes.ok) {
      const probe = (await probeRes.json()) as ProbeResponse;
      if (!probe.direct_play && probe.hls_playlist_url) {
        const hlsUrl = `${env.apiUrl}${probe.hls_playlist_url}`;
        if (env.canPlayHlsNatively()) {
          env.onNativeHls(hlsUrl);
          return;
        }
        try {
          await env.playWithMediabunny({ type: "url", url: hlsUrl }, signal);
          return;
        } catch (err: unknown) {
          const e = err as { name?: string };
          if (e?.name === "AbortError") return;
          // HLS remux failed — fall through to raw direct stream + transcode.
        }
      }
    }
  } catch (err: unknown) {
    const e = err as { name?: string };
    if (e?.name === "AbortError") return;
    // Fall through to direct stream attempt.
  }

  if (signal.aborted) return;
  env.onDirect(env.streamUrl);
}

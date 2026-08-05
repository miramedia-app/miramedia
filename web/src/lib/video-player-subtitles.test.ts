import { describe, expect, it, vi } from "vitest";
import {
  fetchSubtitleTracks,
  loadSubtitlesIfCurrent,
  runPlaybackLoad,
  type PlaybackLoadEnv,
  type SubtitleLoadDeps,
} from "@/lib/video-player-subtitles";

// A resolvable promise handle so tests can control fetch/read ordering.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakeResponse(ok: boolean, body = ""): Response {
  return { ok, text: async () => body } as unknown as Response;
}

// Fake object-URL registry: each create returns a unique url, each revoke logs it.
function fakeUrls() {
  let n = 0;
  const created: string[] = [];
  const revoked: string[] = [];
  const deps: Pick<SubtitleLoadDeps, "createObjectURL" | "revokeObjectURL"> = {
    createObjectURL: () => {
      const url = `blob:${n++}`;
      created.push(url);
      return url;
    },
    revokeObjectURL: (url) => {
      revoked.push(url);
    },
  };
  return { created, revoked, ...deps };
}

const trackUrl = (lang: string) => `/subs/${lang}`;

describe("fetchSubtitleTracks", () => {
  it("starts every language fetch concurrently before any body resolves", async () => {
    const urls = fakeUrls();
    const gates = [deferred<Response>(), deferred<Response>()];
    let started = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const idx = String(input).endsWith("en") ? 0 : 1;
      started++;
      return gates[idx].promise;
    });

    const controller = new AbortController();
    const promise = fetchSubtitleTracks(["en", "de"], trackUrl, controller.signal, {
      fetch: fetchMock as unknown as typeof fetch,
      ...urls,
    });

    // Both requests are in flight before any response arrives — this fails
    // against a serial for-await loop, which starts the 2nd only after the 1st.
    await Promise.resolve();
    expect(started).toBe(2);

    gates[0].resolve(fakeResponse(true, "WEBVTT en"));
    gates[1].resolve(fakeResponse(true, "WEBVTT de"));
    const tracks = await promise;
    expect(tracks.map((t) => t.lang).sort()).toEqual(["de", "en"]);
    expect(urls.created).toHaveLength(2);
  });

  it("tolerates a partial failure without failing the rest", async () => {
    const urls = fakeUrls();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith("de")) return Promise.reject(new Error("boom"));
      return Promise.resolve(fakeResponse(true, "WEBVTT en"));
    });

    const controller = new AbortController();
    const tracks = await fetchSubtitleTracks(["en", "de"], trackUrl, controller.signal, {
      fetch: fetchMock as unknown as typeof fetch,
      ...urls,
    });

    expect(tracks).toEqual([{ lang: "en", url: "blob:0" }]);
    expect(urls.created).toHaveLength(1);
  });

  it("skips non-ok responses without creating a url", async () => {
    const urls = fakeUrls();
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(false)));
    const controller = new AbortController();
    const tracks = await fetchSubtitleTracks(["en"], trackUrl, controller.signal, {
      fetch: fetchMock as unknown as typeof fetch,
      ...urls,
    });
    expect(tracks).toHaveLength(0);
    expect(urls.created).toHaveLength(0);
  });
});

describe("loadSubtitlesIfCurrent", () => {
  it("installs tracks when the load is still current", async () => {
    const urls = fakeUrls();
    const fetchMock = vi.fn(() => Promise.resolve(fakeResponse(true, "WEBVTT")));
    const install = vi.fn();
    const controller = new AbortController();

    await loadSubtitlesIfCurrent({
      languages: ["en"],
      trackUrl,
      signal: controller.signal,
      deps: { fetch: fetchMock as unknown as typeof fetch, ...urls },
      isCurrent: () => true,
      install,
    });

    expect(install).toHaveBeenCalledWith([{ lang: "en", url: "blob:0" }]);
    expect(urls.revoked).toHaveLength(0);
  });

  it("revokes and does not install when aborted before bodies finish", async () => {
    const urls = fakeUrls();
    const gate = deferred<Response>();
    const fetchMock = vi.fn(() => gate.promise);
    const install = vi.fn();
    const controller = new AbortController();

    const promise = loadSubtitlesIfCurrent({
      languages: ["en"],
      trackUrl,
      signal: controller.signal,
      deps: { fetch: fetchMock as unknown as typeof fetch, ...urls },
      isCurrent: () => true,
      install,
    });

    // Body resolves only after the load is aborted (e.g. dialog closed).
    controller.abort();
    gate.resolve(fakeResponse(true, "WEBVTT"));
    await promise;

    expect(install).not.toHaveBeenCalled();
    expect(urls.created).toEqual(["blob:0"]);
    expect(urls.revoked).toEqual(["blob:0"]);
  });

  it("revokes stale results when a superseding load is now current", async () => {
    const urls = fakeUrls();
    const gate = deferred<Response>();
    const fetchMock = vi.fn(() => gate.promise);
    const install = vi.fn();
    const controller = new AbortController();

    const promise = loadSubtitlesIfCurrent({
      languages: ["en"],
      trackUrl,
      signal: controller.signal,
      deps: { fetch: fetchMock as unknown as typeof fetch, ...urls },
      // Not aborted, but a newer load has superseded this one.
      isCurrent: () => false,
      install,
    });

    gate.resolve(fakeResponse(true, "WEBVTT"));
    await promise;

    expect(install).not.toHaveBeenCalled();
    expect(urls.revoked).toEqual(["blob:0"]);
  });

  it("does not reject when every track fails", async () => {
    const urls = fakeUrls();
    const fetchMock = vi.fn(() => Promise.reject(new Error("network")));
    const install = vi.fn();
    const controller = new AbortController();

    await expect(
      loadSubtitlesIfCurrent({
        languages: ["en", "de"],
        trackUrl,
        signal: controller.signal,
        deps: { fetch: fetchMock as unknown as typeof fetch, ...urls },
        isCurrent: () => true,
        install,
      }),
    ).resolves.toBeUndefined();
    expect(install).not.toHaveBeenCalled();
    expect(urls.created).toHaveLength(0);
  });
});

function baseEnv(overrides: Partial<PlaybackLoadEnv> = {}): PlaybackLoadEnv {
  return {
    fetch: vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => ({ direct_play: true }),
      } as unknown as Response),
    ) as unknown as typeof fetch,
    probeUrl: "/stream/probe?file_id=1",
    streamUrl: "/stream?file_id=1",
    apiUrl: "http://api",
    signal: new AbortController().signal,
    startSubtitles: vi.fn(),
    canPlayHlsNatively: () => false,
    playWithMediabunny: vi.fn(() => Promise.resolve()),
    onNativeHls: vi.fn(),
    onDirect: vi.fn(),
    ...overrides,
  };
}

describe("runPlaybackLoad", () => {
  it("probes the stream before subtitle bodies finish", async () => {
    const events: string[] = [];
    const subtitleGate = deferred<void>();
    let probed = false;

    const env = baseEnv({
      startSubtitles: () => {
        events.push("subtitles-started");
        // Simulate subtitle bodies that resolve much later.
        void subtitleGate.promise.then(() => events.push("subtitles-done"));
      },
      fetch: vi.fn(() => {
        probed = true;
        events.push("probe");
        return Promise.resolve({
          ok: true,
          json: async () => ({ direct_play: true }),
        } as unknown as Response);
      }) as unknown as typeof fetch,
    });

    await runPlaybackLoad(env);

    // Playback probed and completed without waiting on subtitle bodies.
    expect(probed).toBe(true);
    expect(events).toEqual(["subtitles-started", "probe"]);
    subtitleGate.resolve();
  });

  it("plays a direct stream on the native element", async () => {
    const env = baseEnv();
    await runPlaybackLoad(env);
    expect(env.onDirect).toHaveBeenCalledWith("/stream?file_id=1");
    expect(env.onNativeHls).not.toHaveBeenCalled();
    expect(env.startSubtitles).toHaveBeenCalledTimes(1);
  });

  it("plays warm-cache HLS natively when supported", async () => {
    const env = baseEnv({
      canPlayHlsNatively: () => true,
      fetch: vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({ direct_play: false, hls_playlist_url: "/hls/x.m3u8" }),
        } as unknown as Response),
      ) as unknown as typeof fetch,
    });

    await runPlaybackLoad(env);
    expect(env.onNativeHls).toHaveBeenCalledWith("http://api/hls/x.m3u8");
    expect(env.onDirect).not.toHaveBeenCalled();
  });

  it("remuxes HLS via Mediabunny when native HLS is unsupported", async () => {
    const env = baseEnv({
      canPlayHlsNatively: () => false,
      playWithMediabunny: vi.fn(() => Promise.resolve()),
      fetch: vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({ direct_play: false, hls_playlist_url: "/hls/x.m3u8" }),
        } as unknown as Response),
      ) as unknown as typeof fetch,
    });

    await runPlaybackLoad(env);
    expect(env.playWithMediabunny).toHaveBeenCalledWith(
      { type: "url", url: "http://api/hls/x.m3u8" },
      env.signal,
    );
    expect(env.onDirect).not.toHaveBeenCalled();
  });

  it("does not install playback state once aborted", async () => {
    const controller = new AbortController();
    const env = baseEnv({
      signal: controller.signal,
      fetch: vi.fn(() => {
        controller.abort();
        return Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      }) as unknown as typeof fetch,
    });

    await runPlaybackLoad(env);
    expect(env.onDirect).not.toHaveBeenCalled();
    expect(env.onNativeHls).not.toHaveBeenCalled();
  });
});

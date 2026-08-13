import { expect, test, type Page } from "@playwright/test";
import { installApiMock, type ApiHandler, type ApiMock } from "./fixtures";

// Playback resume / completion / Start over — rendered dialogs with mocked
// `/api/**` only. HTMLMediaElement is stubbed so no real media or wall-clock
// playback is required. Characterizes serialize (298) and Start-over DELETE (299).

const MOVIE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const FILE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const POSTER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const TITLE = "Fixture Resume Movie";
const DURATION_MS = 600_000;
const SAVED_POSITION_MS = 120_000; // 2:00

type ProgressBody = {
  file_id: string;
  media_kind: string;
  position_ms: number;
  duration_ms: number;
  completed: boolean;
  updated_at: string;
};

function progressBody(
  overrides: Partial<ProgressBody> & Pick<ProgressBody, "position_ms" | "duration_ms">,
): ProgressBody {
  return {
    file_id: FILE_ID,
    media_kind: "movie",
    completed: false,
    updated_at: "2026-08-08T12:00:00Z",
    ...overrides,
  };
}

function continueItem(positionMs = SAVED_POSITION_MS) {
  return {
    file_id: FILE_ID,
    media_kind: "movie" as const,
    media_id: MOVIE_ID,
    show_id: null,
    title: TITLE,
    poster_media_id: POSTER_ID,
    position_ms: positionMs,
    duration_ms: DURATION_MS,
    updated_at: "2026-08-08T12:00:00Z",
  };
}

function movieDetailBundle() {
  return {
    movie: {
      id: MOVIE_ID,
      name: TITLE,
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
        id: FILE_ID,
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
        file_name: "fixture.mp4",
      },
    ],
    subtitles: [],
  };
}

function dashboardShellRoutes(): Record<string, ApiHandler> {
  return {
    "GET /api/v1/dashboard/summary": () => ({
      body: {
        shows: 0,
        movies: 1,
        torrents: 0,
        requests_pending: 0,
        imports_failed: 0,
        imports_ambiguous: 0,
      },
    }),
    "GET /api/v1/shows/recommended": () => ({ body: [] }),
    "GET /api/v1/movies/recommended": () => ({ body: [] }),
    // Movie detail settings sheet / quality labels.
    "GET /api/v1/system/settings": () => ({
      body: { indexers: { quality_options: [], codec_options: [] } },
    }),
    "GET /api/v1/movies/libraries": () => ({ body: [] }),
    // MediaPicture requests; broken poster UI is fine — just must not 501.
    "GET /api/v1/static/image/*": () => ({ status: 404, body: { detail: "no poster" } }),
  };
}

function streamRoutes(): Record<string, ApiHandler> {
  const probePath = `/api/v1/streams/movies/${MOVIE_ID}/probe`;
  const streamPath = `/api/v1/streams/movies/${MOVIE_ID}`;
  return {
    [`GET ${probePath}`]: () => ({
      body: { direct_play: true, container: "mp4", hls_playlist_url: null },
    }),
    // Video element may GET the stream URL; body is unused — media is stubbed.
    [`GET ${streamPath}`]: () => ({
      status: 200,
      contentType: "video/mp4",
      body: "",
    }),
  };
}

type StubMedia = HTMLMediaElement & {
  __mmSrc?: string;
  __mmDuration?: number;
  __mmCurrentTime?: number;
  __mmPaused?: boolean;
  __mmEnded?: boolean;
  __mmArmed?: boolean;
};

/** Deterministic HTMLMediaElement: controllable time/duration, no network media. */
async function installMediaStub(page: Page) {
  await page.addInitScript((durationSec: number) => {
    const proto = HTMLMediaElement.prototype;

    Object.defineProperty(proto, "duration", {
      configurable: true,
      get(this: StubMedia) {
        return this.__mmDuration ?? durationSec;
      },
    });

    Object.defineProperty(proto, "currentTime", {
      configurable: true,
      get(this: StubMedia) {
        return this.__mmCurrentTime ?? 0;
      },
      set(this: StubMedia, value: number) {
        this.__mmCurrentTime = Number(value);
        setTimeout(() => {
          this.dispatchEvent(new Event("seeked"));
          this.dispatchEvent(new Event("timeupdate"));
        }, 0);
      },
    });

    Object.defineProperty(proto, "paused", {
      configurable: true,
      get(this: StubMedia) {
        return this.__mmPaused !== false;
      },
    });

    Object.defineProperty(proto, "ended", {
      configurable: true,
      get(this: StubMedia) {
        return !!this.__mmEnded;
      },
    });

    Object.defineProperty(proto, "networkState", {
      configurable: true,
      get() {
        return 1; // NETWORK_IDLE
      },
    });

    Object.defineProperty(proto, "readyState", {
      configurable: true,
      get() {
        return 4; // HAVE_ENOUGH_DATA
      },
    });

    Object.defineProperty(proto, "error", {
      configurable: true,
      get() {
        return null;
      },
    });

    proto.play = function (this: StubMedia) {
      this.__mmPaused = false;
      queueMicrotask(() => {
        this.dispatchEvent(new Event("play"));
        this.dispatchEvent(new Event("playing"));
      });
      return Promise.resolve();
    };

    proto.pause = function (this: StubMedia) {
      this.__mmPaused = true;
      this.dispatchEvent(new Event("pause"));
    };

    proto.load = function () {
      /* no-op — avoid network media */
    };

    const armReady = (el: StubMedia) => {
      if (!el.__mmSrc || el.__mmArmed) return;
      el.__mmArmed = true;
      el.__mmDuration = durationSec;
      el.__mmPaused = false;
      // setTimeout(0) lands after React passive effects so the progress
      // reporter exists before loadedmetadata/timeupdate handlers run.
      setTimeout(() => {
        el.dispatchEvent(new Event("loadedmetadata"));
        el.dispatchEvent(new Event("loadeddata"));
        el.dispatchEvent(new Event("canplay"));
        el.dispatchEvent(new Event("play"));
        el.dispatchEvent(new Event("playing"));
        el.dispatchEvent(new Event("timeupdate"));
      }, 0);
    };

    // Never assign a network URL to the native media element — that triggers
    // load/error and the player's Mediabunny fallback. Keep src virtual only.
    Object.defineProperty(proto, "src", {
      configurable: true,
      get(this: StubMedia) {
        return this.__mmSrc ?? "";
      },
      set(this: StubMedia, value: string) {
        this.__mmSrc = value;
        this.__mmArmed = false;
        if (value) armReady(this);
      },
    });

    const origSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      if (this instanceof HTMLMediaElement && name === "src") {
        const el = this as StubMedia;
        el.__mmSrc = value == null ? "" : String(value);
        el.__mmArmed = false;
        if (el.__mmSrc) armReady(el);
        return;
      }
      origSetAttribute.call(this, name, value);
    };

    const origGetAttribute = Element.prototype.getAttribute;
    Element.prototype.getAttribute = function (name) {
      if (this instanceof HTMLMediaElement && name === "src") {
        return (this as StubMedia).__mmSrc ?? null;
      }
      return origGetAttribute.call(this, name);
    };
  }, DURATION_MS / 1000);
}

async function videoHandle(page: Page) {
  const video = page.locator("video");
  await expect(video).toBeVisible();
  return video;
}

async function readCurrentTime(page: Page): Promise<number> {
  return page.locator("video").evaluate((el) => (el as HTMLVideoElement).currentTime);
}

async function dispatchMediaEvent(
  page: Page,
  type: "timeupdate" | "pause" | "seeked" | "ended",
  atSec?: number,
) {
  await page.locator("video").evaluate(
    (el, args) => {
      const video = el as HTMLVideoElement & {
        __mmCurrentTime?: number;
        __mmEnded?: boolean;
        __mmPaused?: boolean;
        __mmDuration?: number;
      };
      if (args.atSec != null) video.__mmCurrentTime = args.atSec;
      if (args.type === "ended") {
        video.__mmEnded = true;
        video.__mmCurrentTime = video.__mmDuration ?? video.duration;
      }
      if (args.type === "pause") video.__mmPaused = true;
      video.dispatchEvent(new Event(args.type));
    },
    { type, atSec },
  );
}

function putBodies(mock: ApiMock): Array<{ position_ms: number; duration_ms: number }> {
  return mock.calls
    .filter((c) => c.method === "PUT" && c.pathname === "/api/v1/playback/progress")
    .map((c) => JSON.parse(c.postData ?? "{}") as { position_ms: number; duration_ms: number });
}

test.describe("playback progress browser coverage", () => {
  test("continue watching: Resume seeks to saved position", async ({ page }) => {
    const mock = await installApiMock(page, {
      ...dashboardShellRoutes(),
      ...streamRoutes(),
      "GET /api/v1/playback/continue": () => ({ body: [continueItem()] }),
      "GET /api/v1/playback/progress": () => ({
        body: progressBody({ position_ms: SAVED_POSITION_MS, duration_ms: DURATION_MS }),
      }),
      "PUT /api/v1/playback/progress": (req) => {
        const body = JSON.parse(req.postData() ?? "{}") as {
          position_ms: number;
          duration_ms: number;
        };
        return {
          body: progressBody({
            position_ms: body.position_ms,
            duration_ms: body.duration_ms,
            completed: body.position_ms >= body.duration_ms,
          }),
        };
      },
    });
    await installMediaStub(page);

    await page.goto("/dashboard/");
    await expect(page.getByRole("heading", { name: "Continue Watching" })).toBeVisible();
    await expect(page.getByText(TITLE)).toBeVisible();

    await page
      .getByRole("button")
      .filter({ has: page.locator("svg.lucide-play") })
      .click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await videoHandle(page);

    await expect.poll(() => readCurrentTime(page)).toBeCloseTo(SAVED_POSITION_MS / 1000, 1);

    await expect
      .poll(() => mock.find("GET /api/v1/streams/movies/" + MOVIE_ID + "/probe"))
      .toBeTruthy();
    expect(mock.unhandled).toEqual([]);
  });

  test("movie detail: Resume prompt seeks; PUTs stay ordered; ended is terminal", async ({
    page,
  }) => {
    let releaseFirstPut!: () => void;
    const firstPutHeld = new Promise<void>((resolve) => {
      releaseFirstPut = resolve;
    });
    let putStarts = 0;

    const mock = await installApiMock(page, {
      ...dashboardShellRoutes(),
      ...streamRoutes(),
      "GET /api/v1/playback/continue": () => ({ body: [continueItem()] }),
      [`GET /api/v1/movies/${MOVIE_ID}/detail-bundle`]: () => ({ body: movieDetailBundle() }),
      [`GET /api/v1/movies/${MOVIE_ID}/torrents`]: () => ({ body: [] }),
      "GET /api/v1/playback/progress": () => ({
        body: progressBody({ position_ms: SAVED_POSITION_MS, duration_ms: DURATION_MS }),
      }),
      "PUT /api/v1/playback/progress": async (req) => {
        putStarts += 1;
        const body = JSON.parse(req.postData() ?? "{}") as {
          position_ms: number;
          duration_ms: number;
        };
        // Hold the first PUT so later flush/ended samples queue behind it (298).
        if (putStarts === 1) await firstPutHeld;
        return {
          body: progressBody({
            position_ms: body.position_ms,
            duration_ms: body.duration_ms,
            completed: body.position_ms >= body.duration_ms,
          }),
        };
      },
    });
    await installMediaStub(page);

    await page.goto(`/dashboard/movies/${MOVIE_ID}/`);
    await expect(page.getByRole("heading", { name: TITLE })).toBeVisible();

    // Play button on the imported file row (icon-sized).
    await page
      .getByRole("button")
      .filter({ has: page.locator("svg.lucide-play") })
      .first()
      .click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await videoHandle(page);

    const resumeBtn = page.getByRole("button", { name: /Resume from 2:00/ });
    await expect(resumeBtn).toBeVisible();
    await resumeBtn.click();
    await expect.poll(() => readCurrentTime(page)).toBeCloseTo(SAVED_POSITION_MS / 1000, 1);

    // Queue a mid-play sample, then terminal ended while an earlier PUT is held.
    await expect.poll(() => putStarts).toBeGreaterThanOrEqual(1);
    const startsWhileHeld = putStarts;

    await dispatchMediaEvent(page, "timeupdate", 200);
    await dispatchMediaEvent(page, "pause", 200);
    await dispatchMediaEvent(page, "ended");

    // Serialize (298): no additional PUT fetch starts until the first resolves.
    await page.evaluate(() => new Promise<void>((r) => setTimeout(r, 0)));
    expect(putStarts).toBe(startsWhileHeld);

    releaseFirstPut();

    await expect.poll(() => putBodies(mock).length).toBeGreaterThan(startsWhileHeld);
    const bodies = putBodies(mock);
    for (let i = 1; i < bodies.length; i++) {
      expect(bodies[i]!.position_ms).toBeGreaterThanOrEqual(bodies[i - 1]!.position_ms);
    }
    const last = bodies[bodies.length - 1]!;
    expect(last.position_ms).toBe(last.duration_ms);
    expect(last.duration_ms).toBe(DURATION_MS);

    expect(mock.unhandled).toEqual([]);
  });

  test("Start over DELETEs progress and clears Continue Watching", async ({ page }) => {
    let continueItems = [continueItem()];
    let deleteOk = true;

    const mock = await installApiMock(page, {
      ...dashboardShellRoutes(),
      ...streamRoutes(),
      "GET /api/v1/playback/continue": () => ({ body: continueItems }),
      [`GET /api/v1/movies/${MOVIE_ID}/detail-bundle`]: () => ({ body: movieDetailBundle() }),
      [`GET /api/v1/movies/${MOVIE_ID}/torrents`]: () => ({ body: [] }),
      "GET /api/v1/playback/progress": () => ({
        body:
          continueItems.length > 0
            ? progressBody({ position_ms: SAVED_POSITION_MS, duration_ms: DURATION_MS })
            : null,
      }),
      "PUT /api/v1/playback/progress": (req) => {
        const body = JSON.parse(req.postData() ?? "{}") as {
          position_ms: number;
          duration_ms: number;
        };
        return {
          body: progressBody({
            position_ms: body.position_ms,
            duration_ms: body.duration_ms,
          }),
        };
      },
      "DELETE /api/v1/playback/progress": () => {
        if (!deleteOk) return { status: 500, body: { detail: "upstream failed" } };
        continueItems = [];
        return { status: 204 };
      },
    });
    await installMediaStub(page);

    await page.goto("/dashboard/");
    await expect(page.getByRole("heading", { name: "Continue Watching" })).toBeVisible();

    await page.goto(`/dashboard/movies/${MOVIE_ID}/`);
    await page
      .getByRole("button")
      .filter({ has: page.locator("svg.lucide-play") })
      .first()
      .click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("button", { name: "Start over" })).toBeVisible();

    await page.getByRole("button", { name: "Start over" }).click();

    await expect.poll(() => mock.find("DELETE /api/v1/playback/progress")).toBeTruthy();
    const del = mock.find("DELETE /api/v1/playback/progress");
    expect(del?.url).toContain(`file_id=${FILE_ID}`);
    await expect.poll(() => readCurrentTime(page)).toBeCloseTo(0, 1);

    await page.goto("/dashboard/");
    await expect(page.getByRole("heading", { name: "Continue Watching" })).toHaveCount(0);
    await expect(page.getByText(TITLE)).toHaveCount(0);

    expect(mock.unhandled).toEqual([]);

    // Failure path: DELETE errors stay visible and do not clear continue.
    continueItems = [continueItem()];
    deleteOk = false;
    await page.goto(`/dashboard/movies/${MOVIE_ID}/`);
    await page
      .getByRole("button")
      .filter({ has: page.locator("svg.lucide-play") })
      .first()
      .click();
    await expect(page.getByRole("button", { name: "Start over" })).toBeVisible();
    await page.getByRole("button", { name: "Start over" }).click();

    await expect(page.getByText("Could not clear saved resume position")).toBeVisible();
    expect(continueItems).toHaveLength(1);

    await page.goto("/dashboard/");
    await expect(page.getByRole("heading", { name: "Continue Watching" })).toBeVisible();
    await expect(page.getByText(TITLE)).toBeVisible();

    expect(mock.unhandled).toEqual([]);
  });
});

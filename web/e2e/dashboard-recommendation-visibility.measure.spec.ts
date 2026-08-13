import { expect, test, type Page, type Request } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { installApiMock, type ApiHandler } from "./fixtures";

/**
 * Plan 254 measurement harness (not a CI gate assertion suite).
 *
 * Measures whether recommendation requests are below the fold and whether they
 * delay critical dashboard content (≥100 ms threshold in the plan). Writes a
 * JSON artifact under ../evidence/ for the markdown evidence report.
 */

type TimedCall = {
  key: string;
  startMs: number;
  endMs: number;
  bytes: number;
};

type ViewportCase = {
  name: string;
  width: number;
  height: number;
};

type RunResult = {
  viewport: ViewportCase;
  scenario: string;
  summaryDelayMs: number;
  recommendationDelayMs: number;
  navStartMs: number;
  calls: TimedCall[];
  statsVisibleAtMs: number;
  statsTop: number | null;
  showsCarouselTop: number | null;
  moviesCarouselTop: number | null;
  showsBelowFold: boolean | null;
  moviesBelowFold: boolean | null;
  summaryStartOffsetMs: number | null;
  showsStartOffsetMs: number | null;
  moviesStartOffsetMs: number | null;
  /** How long after summary response until StatCards were visible. */
  statsLagAfterSummaryEndMs: number | null;
  /** True when StatCards painted before both recommendation responses finished. */
  statsBeforeRecommendationsDone: boolean | null;
  /** Max−min of summary/shows/movies request start offsets (ms). */
  requestStartSpreadMs: number | null;
  criticalContentDelayVsFastBaselineMs: number | null;
};

const VIEWPORTS: ViewportCase[] = [
  { name: "mobile", width: 390, height: 844 },
  { name: "desktop", width: 1440, height: 900 },
];

const SAMPLE_CARD = {
  external_id: "tmdb-1",
  metadata_provider: "tmdb",
  name: "Sample Title",
  year: 2024,
  poster_path: null,
  overview: "x".repeat(120),
  imdb_id: "tt0000001",
  added: false,
  id: null,
};

function mediaPayload(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    ...SAMPLE_CARD,
    external_id: `tmdb-${i + 1}`,
    name: `Sample Title ${i + 1}`,
    imdb_id: `tt000000${i + 1}`,
  }));
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

async function measureRun(
  page: Page,
  viewport: ViewportCase,
  scenario: string,
  summaryDelayMs: number,
  recommendationDelayMs: number,
): Promise<RunResult> {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });

  const timed: TimedCall[] = [];
  const t0 = Date.now();

  const wrap =
    (key: string, delayMs: number, body: unknown): ApiHandler =>
    async (_req: Request) => {
      const startMs = Date.now() - t0;
      await sleep(delayMs);
      const payload = JSON.stringify(body);
      const endMs = Date.now() - t0;
      timed.push({ key, startMs, endMs, bytes: Buffer.byteLength(payload) });
      return { body };
    };

  const mock = await installApiMock(page, {
    "GET /api/v1/dashboard/summary": wrap("summary", summaryDelayMs, {
      shows: 12,
      movies: 8,
      torrents: 3,
      requests_pending: 1,
      imports_failed: 0,
      imports_ambiguous: 0,
    }),
    "GET /api/v1/shows/recommended": wrap("shows", recommendationDelayMs, mediaPayload(10)),
    "GET /api/v1/movies/recommended": wrap("movies", recommendationDelayMs, mediaPayload(10)),
  });

  const navStartMs = Date.now() - t0;
  await page.goto("/dashboard/");

  // Primary content: StatCards for superuser (shell fixture is superuser).
  // trailingSlash: true → hrefs end with `/`.
  const stats = page.getByRole("link", { name: /Shows/ }).first();
  await expect(stats).toBeVisible({ timeout: 30_000 });
  const statsVisibleAtMs = Date.now() - t0;

  await expect.poll(() => timed.filter((c) => c.key === "summary").length).toBeGreaterThan(0);
  await expect.poll(() => timed.filter((c) => c.key === "shows").length).toBeGreaterThan(0);
  await expect.poll(() => timed.filter((c) => c.key === "movies").length).toBeGreaterThan(0);

  // Wait for carousel headings once dynamic import settles.
  const showsHeading = page.getByRole("heading", { name: "Trending Shows" });
  const moviesHeading = page.getByRole("heading", { name: "Trending Movies" });
  await expect(showsHeading).toBeVisible({ timeout: 30_000 });
  await expect(moviesHeading).toBeVisible({ timeout: 30_000 });

  const [statsBox, showsBox, moviesBox] = await Promise.all([
    stats.boundingBox(),
    showsHeading.boundingBox(),
    moviesHeading.boundingBox(),
  ]);

  const fold = viewport.height;
  const showsTop = showsBox?.y ?? null;
  const moviesTop = moviesBox?.y ?? null;
  const showsBelowFold = showsTop == null ? null : showsTop >= fold;
  const moviesBelowFold = moviesTop == null ? null : moviesTop >= fold;

  const byKey = (k: string) => timed.find((c) => c.key === k);
  const summary = byKey("summary");
  const shows = byKey("shows");
  const movies = byKey("movies");

  const summaryStartOffsetMs = summary ? summary.startMs - navStartMs : null;
  const showsStartOffsetMs = shows ? shows.startMs - navStartMs : null;
  const moviesStartOffsetMs = movies ? movies.startMs - navStartMs : null;
  const starts = [summaryStartOffsetMs, showsStartOffsetMs, moviesStartOffsetMs].filter(
    (v): v is number => v != null,
  );
  const requestStartSpreadMs =
    starts.length >= 2 ? Math.max(...starts) - Math.min(...starts) : null;

  const statsLagAfterSummaryEndMs = summary != null ? statsVisibleAtMs - summary.endMs : null;
  const recoDoneAt = shows != null && movies != null ? Math.max(shows.endMs, movies.endMs) : null;
  const statsBeforeRecommendationsDone = recoDoneAt != null ? statsVisibleAtMs < recoDoneAt : null;

  expect(mock.unhandled).toEqual([]);

  return {
    viewport,
    scenario,
    summaryDelayMs,
    recommendationDelayMs,
    navStartMs,
    calls: timed,
    statsVisibleAtMs,
    statsTop: statsBox?.y ?? null,
    showsCarouselTop: showsTop,
    moviesCarouselTop: moviesTop,
    showsBelowFold,
    moviesBelowFold,
    summaryStartOffsetMs,
    showsStartOffsetMs,
    moviesStartOffsetMs,
    statsLagAfterSummaryEndMs,
    statsBeforeRecommendationsDone,
    requestStartSpreadMs,
    criticalContentDelayVsFastBaselineMs: null,
  };
}

test.describe.configure({ mode: "serial" });

test("plan 254: measure recommendation visibility / critical-content contention", async ({
  page,
}) => {
  test.skip(
    process.env.MEASURE_254 !== "1",
    "Opt-in measurement harness — run with MEASURE_254=1 (see evidence/254-dashboard-recommendations.md)",
  );
  test.setTimeout(180_000);
  const runs: RunResult[] = [];

  // Warm-cache analogue: cheap recommendation responses (server hit path).
  for (const vp of VIEWPORTS) {
    runs.push(await measureRun(page, vp, "warm-fast-all", 40, 40));
  }

  // Baseline for critical content with fast recommendations (first desktop warm).
  const desktopWarm = runs.find(
    (r) => r.viewport.name === "desktop" && r.scenario === "warm-fast-all",
  );
  expect(desktopWarm).toBeTruthy();
  const baselineStats = desktopWarm!.statsVisibleAtMs;

  // Cold-cache analogue: slow provider fan-out on recommendations only.
  for (const vp of VIEWPORTS) {
    const run = await measureRun(page, vp, "cold-slow-recommendations", 40, 500);
    run.criticalContentDelayVsFastBaselineMs =
      vp.name === desktopWarm!.viewport.name
        ? run.statsVisibleAtMs - baselineStats
        : run.statsVisibleAtMs -
          (runs.find((r) => r.viewport.name === vp.name && r.scenario === "warm-fast-all")
            ?.statsVisibleAtMs ?? run.statsVisibleAtMs);
    runs.push(run);
  }

  // Contending case: summary itself is slow — proves we measure critical path.
  runs.push(await measureRun(page, VIEWPORTS[1]!, "slow-summary-control", 500, 40));

  // Pairwise mobile critical-content delta (warm vs cold) within this session.
  const mobileWarm = runs.find(
    (r) => r.viewport.name === "mobile" && r.scenario === "warm-fast-all",
  )!;
  const mobileCold = runs.find(
    (r) => r.viewport.name === "mobile" && r.scenario === "cold-slow-recommendations",
  )!;
  mobileCold.criticalContentDelayVsFastBaselineMs =
    mobileCold.statsVisibleAtMs - mobileWarm.statsVisibleAtMs;

  const desktopCold = runs.find(
    (r) => r.viewport.name === "desktop" && r.scenario === "cold-slow-recommendations",
  )!;
  desktopCold.criticalContentDelayVsFastBaselineMs =
    desktopCold.statsVisibleAtMs - desktopWarm!.statsVisibleAtMs;

  const outDir = path.resolve(__dirname, "../../evidence");
  fs.mkdirSync(outDir, { recursive: true });
  const artifact = {
    measuredAt: new Date().toISOString(),
    environment: {
      tool: "playwright",
      browser: "chromium",
      note: "API mocked via e2e/fixtures; delays simulate warm vs cold recommendation latency",
      serverCacheDoc:
        "shows/movies /recommended use 1h in-process provider cache; annotate on every hit",
    },
    thresholdMs: 100,
    runs,
  };
  const outPath = path.join(outDir, "254-dashboard-recommendations.raw.json");
  fs.writeFileSync(outPath, JSON.stringify(artifact, null, 2));

  // Soft characterization — always pass; decision lives in the evidence markdown.
  expect(runs.length).toBeGreaterThanOrEqual(4);
  console.log(`Wrote measurement artifact: ${outPath}`);
});

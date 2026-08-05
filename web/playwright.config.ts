import { defineConfig, devices } from "@playwright/test";

// Browser smoke suite for critical media mutation workflows.
//
// Deliberately tiny: Chromium only, no live backend. Every `/api/**` request is
// intercepted inside the specs (see `e2e/fixtures.ts`) so the suite exercises
// the rendered dialogs / row actions and asserts the outgoing request contracts
// without a running FastAPI/PostgreSQL stack.
//
// The pages under test are client-rendered (`"use client"`) routes of the
// static-export SPA, so `next dev` serves them and interception is installed
// before the first navigation. Vitest owns `src/**/*.test.ts`; these live under
// `e2e/` and are never picked up twice.
// Dedicated, uncommon port so the suite never reuses an unrelated `next dev`
// (e.g. the local dev stack on :3000/:3100) via `reuseExistingServer`.
const PORT = Number(process.env.PLAYWRIGHT_PORT ?? 43117);
// Use `localhost` (not 127.0.0.1): Next 16 dev blocks cross-origin requests to
// its `/_next/*` dev resources, and a mismatched host stalls hydration.
const HOST = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: HOST,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Same-origin API base (NEXT_PUBLIC_API_URL unset) so `/api/**` stays
    // relative and the specs' route handlers can match it.
    command: `pnpm exec next dev --port ${PORT}`,
    url: HOST,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});

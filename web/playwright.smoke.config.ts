import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// Real frontend → FastAPI → PostgreSQL smoke path.
//
// Unlike `playwright.config.ts`, this project does NOT intercept `/api/**`.
// A disposable stack (`scripts/smoke_stack.py`) migrates a throwaway database,
// seeds a verified superuser, serves the static export from FastAPI, and tears
// everything down when the run finishes.
//
// Ports / health:
//   SMOKE_PORT (default 43219) — combined SPA + API origin
//   GET http://localhost:<port>/api/v1/health — db.ok must be true before tests start
const PORT = Number(process.env.SMOKE_PORT ?? 43219);
const HOST = `http://localhost:${PORT}`;
const REPO_ROOT = path.resolve(__dirname, "..");
const CREDS_FILE = process.env.SMOKE_CREDS_FILE ?? path.join(__dirname, ".smoke-credentials.json");

export default defineConfig({
  testDir: "./e2e-smoke",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  outputDir: "test-results-smoke",
  use: {
    baseURL: HOST,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `uv run --python 3.13 python scripts/smoke_stack.py --port ${PORT}`,
    cwd: REPO_ROOT,
    url: `${HOST}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 240_000,
    env: {
      ...process.env,
      PYTHONPATH: REPO_ROOT,
      SMOKE_PORT: String(PORT),
      SMOKE_CREDS_FILE: CREDS_FILE,
      FRONTEND_FILES_DIR: process.env.FRONTEND_FILES_DIR ?? path.join(__dirname, "out"),
    },
  },
});

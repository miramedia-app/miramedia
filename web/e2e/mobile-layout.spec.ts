import { expect, test } from "@playwright/test";
import { installApiMock } from "./fixtures";
import { SHOW_ID, createWatchlistMockState, watchlistApiRoutes } from "./watchlist-fixtures";

// Mobile layout guard: every dashboard route must fit the phone viewport with
// no horizontal overflow (portrait and landscape projects). Mocked `/api/**`
// only; unhandled endpoints answer 501 and pages render their error/empty
// state, which must also not overflow.

const ROUTES = [
  "/dashboard/",
  "/dashboard/shows/",
  "/dashboard/movies/",
  "/dashboard/torrents/",
  "/dashboard/imports/",
  "/dashboard/requests/",
  "/dashboard/system/logs/",
  "/dashboard/system/settings/",
  "/dashboard/system/diagnostics/",
  `/dashboard/shows/${SHOW_ID}/`,
];

const listRoutes = {
  "GET /api/v1/shows": () => ({ body: [] }),
  "GET /api/v1/movies": () => ({ body: [] }),
  "GET /api/v1/torrents": () => ({ body: [] }),
  "GET /api/v1/imports/queue": () => ({ body: { total: 0, offset: 0, limit: 200, items: [] } }),
  "GET /api/v1/requests": () => ({ body: [] }),
  "GET /api/v1/system/logs": () => ({ body: { items: [], total: 0 } }),
  "GET /api/v1/diagnostics/storage": () => ({
    body: {
      generated_at: new Date().toISOString(),
      integrity_check_enabled: false,
      integrity_check_interval_hours: 168,
      freshness_note: "",
      counts: {
        imported: 0,
        healthy: 0,
        unknown: 0,
        corrupt: 0,
        orphaned: 0,
        pending: 0,
        missing: null,
      },
      libraries: [],
      unconfigured_library_names: [],
      volumes: [],
    },
  }),
  "GET /api/v1/diagnostics/storage/files": () => ({
    body: { items: [], total: 0, offset: 0, limit: 50, next_offset: null },
  }),
  "GET /api/v1/diagnostics/database": () => ({
    body: {
      generated_at: new Date().toISOString(),
      host: "localhost",
      port: 5432,
      name: "miramedia",
      user: "miramedia",
      connections: [],
      pools: [],
      largest_tables: [],
    },
  }),
  "GET /api/v1/diagnostics/scheduler": () => ({
    body: {
      generated_at: new Date().toISOString(),
      tasks: [],
      schedules_loaded: false,
    },
  }),
};

test("mobile layout: More sheet shows Diagnostics for superusers", async ({ page }) => {
  const state = createWatchlistMockState();
  state.session.loggedOut = false;
  await installApiMock(page, { ...watchlistApiRoutes(state), ...listRoutes });

  await page.goto("/dashboard/");
  await expect(page).not.toHaveURL(/\/login\/?$/);
  await page.getByRole("button", { name: "More", exact: true }).click();
  const diagnostics = page.getByRole("link", { name: "Diagnostics" });
  await diagnostics.scrollIntoViewIfNeeded();
  await expect(diagnostics).toBeVisible();
});

for (const route of ROUTES) {
  test(`mobile layout: ${route} has no horizontal overflow`, async ({ page }) => {
    const state = createWatchlistMockState();
    // The watchlist state boots logged-out (its own spec drives the login
    // form); flip it so `/users/me` answers 200 and the dashboard shell renders
    // instead of redirecting every route to `/login/`.
    state.session.loggedOut = false;
    await installApiMock(page, { ...watchlistApiRoutes(state), ...listRoutes });

    await page.goto(route);
    await expect(page).not.toHaveURL(/\/login\/?$/);
    await expect(page.locator("[data-slot=sidebar-inset]").first()).toBeVisible();
    // Let data queries settle so late-rendering content is measured too.
    await page.waitForLoadState("networkidle");

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflow.scrollWidth, `document wider than viewport on ${route}`).toBeLessThanOrEqual(
      overflow.innerWidth,
    );
  });
}

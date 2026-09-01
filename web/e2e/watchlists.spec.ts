import { expect, test } from "@playwright/test";

import { installApiMock } from "./fixtures";
import {
  FIXTURE_PASSWORD,
  MOVIE_ID,
  MOVIE_NAME,
  SHOW_ID,
  SHOW_NAME,
  USER_A_EMAIL,
  USER_A_ID,
  USER_B_EMAIL,
  createWatchlistMockState,
  watchlistApiRoutes,
} from "./watchlist-fixtures";

// Private watchlists: hub My Lists, Watch Next, Upcoming, and isolation.

async function login(page: import("@playwright/test").Page, email: string) {
  await page.goto("/login/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(FIXTURE_PASSWORD);
  await Promise.all([
    page.waitForURL(/\/dashboard\/?/),
    page.getByRole("button", { name: "Login", exact: true }).click(),
  ]);
}

async function logout(page: import("@playwright/test").Page, email: string) {
  await page.getByRole("button", { name: new RegExp(email) }).click();
  await Promise.all([
    page.waitForURL(/\/login\/?/),
    page.getByRole("menuitem", { name: "Log Out" }).click(),
  ]);
}

async function addToWeekendList(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Watchlists" }).click();
  await expect(page.getByRole("dialog", { name: "Add to Watchlist" })).toBeVisible();
  await page.getByRole("combobox", { name: "Watchlist" }).click();
  await page.getByRole("option", { name: "Weekend" }).click();
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Added to watchlist")).toBeVisible();
}

test.describe("watchlists browser coverage", () => {
  test("hub, Watch Next, Upcoming, and user isolation", async ({ page }) => {
    const state = createWatchlistMockState();
    const mock = await installApiMock(page, watchlistApiRoutes(state));

    await login(page, USER_A_EMAIL);

    await page.goto("/dashboard/watchlists/");
    await expect(page.getByPlaceholder("Search or filter watchlists…")).toBeVisible();
    await expect(page.getByRole("button", { name: /Name A/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Add Watchlist" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Watch Next/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Upcoming/ })).toBeVisible();

    await page.getByRole("button", { name: "Add Watchlist" }).click();
    await page.getByLabel("Name").fill("Weekend");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard\/watchlists\/[0-9a-f-]+\/?$/i);
    await expect(page.getByRole("heading", { level: 1, name: "Weekend" })).toBeVisible();

    await page.goto(`/dashboard/shows/${SHOW_ID}/`);
    await expect(page.getByRole("heading", { level: 1, name: SHOW_NAME })).toBeVisible();
    await addToWeekendList(page);

    await page.goto(`/dashboard/movies/${MOVIE_ID}/`);
    await expect(page.getByRole("heading", { level: 1, name: MOVIE_NAME })).toBeVisible();
    await addToWeekendList(page);

    await page.goto("/dashboard/watchlists/");
    await page.getByRole("link", { name: /Weekend/ }).click();
    await expect(page).toHaveURL(/\/dashboard\/watchlists\/[0-9a-f-]+\/?$/i);
    const watchlistItems = page.locator("main ul.divide-y");
    const rows = watchlistItems.getByRole("listitem");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText(SHOW_NAME);
    await expect(rows.nth(1)).toContainText(MOVIE_NAME);
    await expect(rows.nth(0).getByRole("button", { name: "Move down" })).toBeEnabled();
    await rows.nth(0).getByRole("button", { name: "Move down" }).click();
    await expect(rows.nth(0)).toContainText(MOVIE_NAME);
    await expect(rows.nth(1)).toContainText(SHOW_NAME);
    await expect
      .poll(() =>
        mock.calls.some((call) => call.method === "PUT" && call.pathname.endsWith("/items/order")),
      )
      .toBe(true);

    await page.goto("/dashboard/watchlists/watch-next/");
    await expect(page.getByRole("heading", { level: 1, name: "Watch Next" })).toBeVisible();
    await expect(page.getByText("S01E01")).toBeVisible();
    await page.getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menuitem", { name: "Mark watched" }).click();
    await expect(page.getByText("Marked as watched")).toBeVisible();
    await page.reload();
    await expect(page.getByText("S01E02")).toBeVisible();
    await expect(page.getByText("S01E01")).toHaveCount(0);

    await page.goto("/dashboard/watchlists/");
    await page.getByRole("link", { name: /Upcoming/ }).click();
    await expect(page).toHaveURL(/\/dashboard\/watchlists\/upcoming\/?/);
    await expect(page.getByText(SHOW_NAME).first()).toBeVisible();
    await expect(page.getByText("S01E02")).toBeVisible();

    await logout(page, USER_A_EMAIL);
    await login(page, USER_B_EMAIL);

    await page.goto("/dashboard/watchlists/");
    await expect(page.getByText("Weekend")).toHaveCount(0);
    await page.getByRole("link", { name: /Watch Next/ }).click();
    await expect(page.getByText("Nothing queued yet")).toBeVisible();
    await page.goto("/dashboard/watchlists/");
    await page.getByRole("link", { name: /Upcoming/ }).click();
    await expect(page.getByText(SHOW_NAME).first()).toBeVisible();
    await expect(page.getByText("S01E02")).toBeVisible();

    expect(mock.unhandled).toEqual([]);
  });

  test("watch next disabled flag and load-failure retry", async ({ page }) => {
    const state = createWatchlistMockState();
    const disabledMock = await installApiMock(page, {
      ...watchlistApiRoutes(state),
      "GET /api/v1/features": () => ({
        body: {
          requests: false,
          subtitles: false,
          notifications: true,
          watchlists: true,
          custom_lists: true,
          watch_next: false,
          watch_next_include_specials: false,
          upcoming: true,
          upcoming_default_past_days: 0,
          upcoming_default_future_days: 30,
          continue_watching: true,
        },
      }),
    });
    await login(page, USER_A_EMAIL);
    await page.goto("/dashboard/watchlists/watch-next/");
    await expect(page.getByText("Watch Next is disabled")).toBeVisible();
    await expect(page.getByText("Enable it in System → Settings → Watchlists.")).toBeVisible();
    expect(disabledMock.unhandled).toEqual([]);
  });

  test("watch next error state offers retry that recovers", async ({ page }) => {
    const state = createWatchlistMockState();
    state.users[USER_A_ID]!.trackedShows.add(SHOW_ID);
    const routes = watchlistApiRoutes(state);
    const originalWatchNext = routes["GET /api/v1/playback/watch-next"];
    let failWatchNext = true;
    const mock = await installApiMock(page, {
      ...routes,
      "GET /api/v1/playback/watch-next": (req) => {
        if (failWatchNext) {
          return { status: 500, body: { detail: "boom" } };
        }
        return originalWatchNext!(req);
      },
    });
    await login(page, USER_A_EMAIL);
    await page.goto("/dashboard/watchlists/watch-next/");
    await expect(page.getByText("Watch Next could not be loaded")).toBeVisible();
    failWatchNext = false;
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("S01E01")).toBeVisible();
    expect(mock.unhandled).toEqual([]);
  });
});

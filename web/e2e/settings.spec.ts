import { expect, test } from "@playwright/test";
import { installApiMock } from "./fixtures";
import {
  FRONTEND_URL_DEFAULT,
  FRONTEND_URL_OVERRIDE,
  SECRET_MASK,
  TMDB_API_KEY_FIXTURE,
  createSettingsMockState,
  settingsApiRoutes,
} from "./settings-fixtures";

// Settings control-plane: load → edit scalar + nested secret → save; reset;
// rejected read with retry (plan 242); rejected save retains dirty; malformed
// import mutates nothing. Mocked `/api/**` only — fixtures reject unhandled paths.

const SETTINGS_PATH = "/dashboard/system/settings/";
const EDITED_FRONTEND_URL = "https://edited.example.com";

async function gotoSettingsReady(page: import("@playwright/test").Page) {
  await page.goto(SETTINGS_PATH);
  await expect(page.getByRole("tab", { name: "General" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Save$/ })).toBeVisible();
}

function frontendUrlInput(page: import("@playwright/test").Page) {
  // Labels are siblings of inputs (no htmlFor); use the unique placeholder.
  return page.getByPlaceholder("https://miramedia.example.com");
}

function tmdbApiKeyInput(page: import("@playwright/test").Page) {
  return page.getByPlaceholder("Enter your TMDB API key");
}

test("settings: load, edit scalar + nested secret, save exact payload", async ({ page }) => {
  const state = createSettingsMockState();
  const mock = await installApiMock(page, settingsApiRoutes(state));

  await gotoSettingsReady(page);

  await expect(page.getByRole("tab", { name: "General" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Metadata" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Authentication" })).toBeVisible();
  await expect(frontendUrlInput(page)).toHaveValue(FRONTEND_URL_OVERRIDE);
  await expect(page.getByText("overridden").first()).toBeVisible();

  // Scalar edit on General.
  await frontendUrlInput(page).fill(EDITED_FRONTEND_URL);
  await expect(page.getByText(/unsaved section/)).toBeVisible();

  // Nested secret-aware field on Metadata (masked on load → type a redacted fake).
  await page.getByRole("tab", { name: "Metadata" }).click();
  await expect(page.getByText("Metadata Settings")).toBeVisible();
  await expect(tmdbApiKeyInput(page)).toHaveValue("");
  await tmdbApiKeyInput(page).fill(TMDB_API_KEY_FIXTURE);
  await expect(page.getByText(/2 unsaved sections/)).toBeVisible();

  await page.getByRole("button", { name: /^Save$/ }).click();

  await expect.poll(() => mock.find("PUT /api/v1/system/settings")).toBeTruthy();
  const put = mock.find("PUT /api/v1/system/settings");
  const body = JSON.parse(put?.postData ?? "{}");

  // Exact outgoing contract: edited fields + preserved unrelated sections/fields.
  expect(body.misc.frontend_url).toBe(EDITED_FRONTEND_URL);
  expect(body.misc.cors_urls).toEqual(["http://localhost:8000"]);
  expect(body.metadata.tmdb.api_key).toBe(TMDB_API_KEY_FIXTURE);
  expect(body.metadata.tmdb.enabled).toBe(true);
  expect(body.metadata.tmdb.default_language).toBe("en");
  expect(body.metadata.desired_languages).toEqual(["en"]);
  expect(body.cloudflare).toMatchObject({ solver: "native" });
  // Only dirty sections are sent (see use-settings-editor buildPayload): this
  // test edits General (misc + cloudflare) and Metadata, so no other section
  // is present in the payload.
  expect(body.auth).toBeUndefined();
  expect(body.notifications).toBeUndefined();
  expect(body.torrents).toBeUndefined();
  expect(body.indexers).toBeUndefined();
  expect(body.requests).toBeUndefined();
  expect(body.subtitles).toBeUndefined();
  expect(body.imports).toBeUndefined();
  expect(body.updates).toBeUndefined();
  // Masked sentinel must not be what we asserted above for the edited key.
  expect(body.metadata.tmdb.api_key).not.toBe(SECRET_MASK);

  await expect(page.getByText(/unsaved section/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();

  // Refetch after save must not leave gaps.
  await expect
    .poll(
      () =>
        mock.calls.filter((c) => c.method === "GET" && c.pathname === "/api/v1/system/settings")
          .length,
    )
    .toBeGreaterThanOrEqual(2);

  expect(mock.unhandled).toEqual([]);
});

test("settings: reset override restores default", async ({ page }) => {
  const state = createSettingsMockState();
  const mock = await installApiMock(page, settingsApiRoutes(state));

  await gotoSettingsReady(page);
  await expect(frontendUrlInput(page)).toHaveValue(FRONTEND_URL_OVERRIDE);

  await page.getByRole("button", { name: "Reset misc.frontend_url to default" }).click();

  await expect.poll(() => mock.find("POST /api/v1/system/settings/override/clear")).toBeTruthy();
  const clear = mock.find("POST /api/v1/system/settings/override/clear");
  expect(JSON.parse(clear?.postData ?? "{}")).toEqual({
    path: ["misc", "frontend_url"],
  });

  await expect(frontendUrlInput(page)).toHaveValue(FRONTEND_URL_DEFAULT);
  await expect(
    page.getByRole("button", { name: "Reset misc.frontend_url to default" }),
  ).toHaveCount(0);

  expect(mock.unhandled).toEqual([]);
});

test("settings: rejected initial read shows retry then loads", async ({ page }) => {
  const state = createSettingsMockState();
  // Stay failing across React Query retries / Strict Mode remounts until Retry.
  state.failSettingsGet = true;
  const mock = await installApiMock(page, settingsApiRoutes(state));

  await page.goto(SETTINGS_PATH);

  await expect(page.getByText("Failed to load settings")).toBeVisible();
  await expect(page.getByText("Check that the server is reachable and try again.")).toBeVisible();
  // Plan 242: never perpetual skeleton — retry is actionable.
  const retry = page.getByRole("button", { name: "Retry" });
  await expect(retry).toBeVisible();
  await expect(page.getByRole("tab", { name: "General" })).toHaveCount(0);

  state.failSettingsGet = false;
  await retry.click();

  await expect(page.getByRole("tab", { name: "General" })).toBeVisible();
  await expect(frontendUrlInput(page)).toHaveValue(FRONTEND_URL_OVERRIDE);

  const settingsGets = mock.calls.filter(
    (c) => c.method === "GET" && c.pathname === "/api/v1/system/settings",
  );
  expect(settingsGets.length).toBeGreaterThanOrEqual(2);

  expect(mock.unhandled).toEqual([]);
});

test("settings: rejected save retains dirty state", async ({ page }) => {
  const state = createSettingsMockState();
  state.settingsPutFailuresRemaining = 1;
  const mock = await installApiMock(page, settingsApiRoutes(state));

  await gotoSettingsReady(page);
  await frontendUrlInput(page).fill(EDITED_FRONTEND_URL);
  await expect(page.getByText(/unsaved section/)).toBeVisible();

  await page.getByRole("button", { name: /^Save$/ }).click();

  await expect.poll(() => mock.find("PUT /api/v1/system/settings")).toBeTruthy();
  await expect(page.getByText("Failed to save settings")).toBeVisible();

  // Dirty retained — value and unsaved indicator stay; Save still enabled.
  await expect(frontendUrlInput(page)).toHaveValue(EDITED_FRONTEND_URL);
  await expect(page.getByText(/unsaved section/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Save$/ })).toBeEnabled();

  expect(mock.unhandled).toEqual([]);
});

test("settings: malformed import does not mutate", async ({ page }) => {
  const state = createSettingsMockState();
  const mock = await installApiMock(page, settingsApiRoutes(state));

  await gotoSettingsReady(page);
  const urlBefore = FRONTEND_URL_OVERRIDE;
  await expect(frontendUrlInput(page)).toHaveValue(urlBefore);

  page.once("filechooser", async (chooser) => {
    await chooser.setFiles({
      name: "broken-settings.json",
      mimeType: "application/json",
      buffer: Buffer.from("{ not valid json", "utf-8"),
    });
  });

  await page.getByRole("button", { name: "Import" }).click();

  await expect(page.getByText("Invalid JSON file")).toBeVisible();
  await expect(frontendUrlInput(page)).toHaveValue(urlBefore);

  // No import (or save/clear) request — client rejects before mutation.
  expect(mock.find("POST /api/v1/system/settings/import")).toBeUndefined();
  expect(mock.find("PUT /api/v1/system/settings")).toBeUndefined();
  expect(mock.find("DELETE /api/v1/system/settings")).toBeUndefined();

  expect(mock.unhandled).toEqual([]);
});

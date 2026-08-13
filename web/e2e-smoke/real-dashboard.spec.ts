import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type SmokeCredentials = {
  email: string;
  password: string;
};

function loadSmokeCredentials(): SmokeCredentials {
  const credsPath =
    process.env.SMOKE_CREDS_FILE ?? path.join(__dirname, "..", ".smoke-credentials.json");
  const raw = fs.readFileSync(credsPath, "utf8");
  const parsed = JSON.parse(raw) as Partial<SmokeCredentials>;
  if (!parsed.email || !parsed.password) {
    throw new Error(`Smoke credentials file at ${credsPath} is incomplete`);
  }
  return { email: parsed.email, password: parsed.password };
}

test("real stack: login, session cookie, and dashboard summary from FastAPI", async ({ page }) => {
  const credentials = loadSmokeCredentials();

  await expect
    .poll(async () => {
      const response = await page.request.get("/api/v1/health");
      if (!response.ok()) return false;
      const body = (await response.json()) as { db?: { ok?: boolean } };
      return body.db?.ok === true;
    })
    .toBe(true);

  await page.goto("/login/");
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();

  const summaryResponse = page.waitForResponse(
    (response) => response.url().includes("/api/v1/dashboard/summary") && response.status() === 200,
  );

  await page.getByLabel("Email").fill(credentials.email);
  await page.getByLabel("Password", { exact: true }).fill(credentials.password);

  await Promise.all([
    page.waitForURL(/\/dashboard\/?/),
    page.getByRole("button", { name: "Login", exact: true }).click(),
  ]);

  const cookies = await page.context().cookies();
  expect(cookies.some((cookie) => cookie.value.length > 0)).toBeTruthy();

  const meResponse = await page.request.get("/api/v1/users/me");
  expect(meResponse.ok()).toBeTruthy();
  const me = (await meResponse.json()) as { email?: string; is_superuser?: boolean };
  expect(me.email).toBe(credentials.email);
  expect(me.is_superuser).toBe(true);

  const summary = await (await summaryResponse).json();
  expect(summary).toMatchObject({
    shows: 0,
    movies: 0,
    torrents: 0,
    imports_failed: 0,
    imports_ambiguous: 0,
  });

  await expect(page.getByRole("link", { name: "Shows", exact: true })).toBeVisible();
  await expect(page.getByText("Total tracked shows")).toBeVisible();
});

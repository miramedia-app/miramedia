import { expect, test } from "@playwright/test";
import { installApiMock } from "./fixtures";

test("sidebar primary nav is labeled Application", async ({ page }) => {
  await installApiMock(page, {
    "GET /api/v1/shows/recommended": () => ({ body: [] }),
    "GET /api/v1/movies/recommended": () => ({ body: [] }),
  });

  await page.goto("/dashboard/");
  await expect(page).not.toHaveURL(/\/login\/?$/);

  const firstGroup = page.locator("[data-slot=sidebar-group]").first();
  await expect(firstGroup).toBeVisible();
  await expect(firstGroup.locator("[data-sidebar=group-label]")).toHaveText("Application");
});

test("sidebar includes Diagnostics for superusers", async ({ page }) => {
  await installApiMock(page, {
    "GET /api/v1/shows/recommended": () => ({ body: [] }),
    "GET /api/v1/movies/recommended": () => ({ body: [] }),
  });

  await page.goto("/dashboard/");
  await expect(page).not.toHaveURL(/\/login\/?$/);

  const diagnostics = page.getByRole("link", { name: "Diagnostics" });
  await diagnostics.scrollIntoViewIfNeeded();
  await expect(diagnostics).toBeVisible();
  await expect(diagnostics).toHaveAttribute("href", "/dashboard/system/diagnostics/");
});

test("diagnostics has settings-style tabs and no page title", async ({ page }) => {
  await installApiMock(page, {
    "GET /api/v1/shows/recommended": () => ({ body: [] }),
    "GET /api/v1/movies/recommended": () => ({ body: [] }),
  });

  await page.goto("/dashboard/system/diagnostics/");
  await expect(page).not.toHaveURL(/\/login\/?$/);
  await expect(page.getByRole("tab", { name: "Storage" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Database" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scheduled Tasks" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Diagnostics" })).toHaveCount(0);
  await expect(
    page.getByText("Read-only storage, database, and scheduler snapshot for operators."),
  ).toHaveCount(0);
});

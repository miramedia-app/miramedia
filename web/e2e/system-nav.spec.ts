import { expect, test } from "@playwright/test";
import { installApiMock } from "./fixtures";

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

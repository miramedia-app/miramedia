import { expect, test } from "@playwright/test";
import { authEntryRoutes, installApiMock, unauthorizedMe } from "./fixtures";

// Authentication entry: password login, registration gating, and one OIDC
// provider — all through the rendered login card with mocked `/api/**` only.
// Non-secret fixture credentials; hard navigations stay on the local origin.

const FIXTURE_EMAIL = "auth-user@example.com";
const FIXTURE_PASSWORD = "fixture-password-not-a-secret";
const OIDC_PROVIDER = "ExampleOIDC";
/** Same-origin stub so OAuth hardNavigate never leaves the Playwright host. */
const OAUTH_AUTHORIZE_PATH = "/oauth-provider-stub";

test("authentication: password login succeeds and navigates to the dashboard", async ({ page }) => {
  const mock = await installApiMock(page, {
    ...authEntryRoutes({ allowRegistration: false }),
    "POST /api/v1/auth/cookie/login": () => ({ status: 204 }),
    // Dashboard home fetches recommendations after AuthGate clears.
    "GET /api/v1/shows/recommended": () => ({ body: [] }),
    "GET /api/v1/movies/recommended": () => ({ body: [] }),
  });

  await page.goto("/login/");
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();

  await page.getByLabel("Email").fill(FIXTURE_EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(FIXTURE_PASSWORD);

  await Promise.all([
    page.waitForURL(/\/dashboard\/?/),
    page.getByRole("button", { name: "Login", exact: true }).click(),
  ]);

  await expect.poll(() => mock.find("POST /api/v1/auth/cookie/login")).toBeTruthy();
  const login = mock.find("POST /api/v1/auth/cookie/login");
  expect(login?.contentType).toMatch(/application\/x-www-form-urlencoded/i);
  const body = new URLSearchParams(login?.postData ?? "");
  expect(body.get("username")).toBe(FIXTURE_EMAIL);
  expect(body.get("password")).toBe(FIXTURE_PASSWORD);
  expect(body.get("scope")).toBe("");

  // Let dashboard shell + home queries settle before asserting no gaps.
  await expect.poll(() => mock.find("GET /api/v1/shows/recommended")).toBeTruthy();
  await expect.poll(() => mock.find("GET /api/v1/movies/recommended")).toBeTruthy();

  expect(mock.unhandled).toEqual([]);
});

test("authentication: rejected credentials show a recoverable error", async ({ page }) => {
  const mock = await installApiMock(page, {
    ...authEntryRoutes({ me: unauthorizedMe }),
    "POST /api/v1/auth/cookie/login": () => ({
      status: 400,
      body: { detail: "LOGIN_BAD_CREDENTIALS" },
    }),
  });

  await page.goto("/login/");
  await page.getByLabel("Email").fill(FIXTURE_EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(FIXTURE_PASSWORD);
  await page.getByRole("button", { name: "Login", exact: true }).click();

  await expect(
    page.getByText("Login failed! Please check your credentials and try again."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeEnabled();
  await expect(page).toHaveURL(/\/login\/?/);

  expect(mock.unhandled).toEqual([]);
});

test("authentication: signup link is hidden when registration is disabled", async ({ page }) => {
  const mock = await installApiMock(page, {
    ...authEntryRoutes({ allowRegistration: false, me: unauthorizedMe }),
  });

  await page.goto("/login/");
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /sign up/i })).toHaveCount(0);

  expect(mock.unhandled).toEqual([]);
});

test("authentication: signup link appears when registration is allowed", async ({ page }) => {
  const mock = await installApiMock(page, {
    ...authEntryRoutes({ allowRegistration: true, me: unauthorizedMe }),
  });

  await page.goto("/login/");
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();
  const signupLink = page.getByRole("link", { name: /sign up/i });
  await expect(signupLink).toBeVisible();
  await expect(signupLink).toHaveAttribute("href", /\/login\/signup\/?/);

  expect(mock.unhandled).toEqual([]);
});

test("authentication: OIDC provider button requests authorize and navigates locally", async ({
  page,
}) => {
  const mock = await installApiMock(page, {
    ...authEntryRoutes({
      oauthProviders: [OIDC_PROVIDER],
      me: unauthorizedMe,
    }),
    "GET /api/v1/auth/oauth/authorize": () => ({
      body: { authorization_url: OAUTH_AUTHORIZE_PATH },
    }),
  });

  await page.goto("/login/");
  const oidcButton = page.getByRole("button", { name: `Login with ${OIDC_PROVIDER}` });
  await expect(oidcButton).toBeVisible();

  // commit: stub path is not a real Next route and may never reach "load".
  await Promise.all([
    page.waitForURL(/\/oauth-provider-stub\/?/, { waitUntil: "commit" }),
    oidcButton.click(),
  ]);

  await expect.poll(() => mock.find("GET /api/v1/auth/oauth/authorize")).toBeTruthy();
  const authorize = mock.find("GET /api/v1/auth/oauth/authorize");
  expect(authorize?.url).toMatch(/scopes=/);
  expect(authorize?.url).toMatch(/openid/);

  expect(mock.unhandled).toEqual([]);
});

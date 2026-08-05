import type { Page, Request } from "@playwright/test";

// Deterministic API/auth interception for the browser smoke suite.
//
// No backend runs: every `/api/**` request is answered here. A request whose
// `METHOD /pathname` has no registered handler is recorded in `unhandled` and
// answered with 501, so a spec that asserts `expect(mock.unhandled).toEqual([])`
// fails the moment a page grows a new, unstubbed API dependency — fixtures can
// never silently mask one.

export interface FulfillSpec {
  status?: number;
  /** JSON-serializable body, or a raw string when `contentType` is set. */
  body?: unknown;
  contentType?: string;
  headers?: Record<string, string>;
}

export type ApiHandler = (req: Request) => FulfillSpec | Promise<FulfillSpec>;

export interface RecordedCall {
  method: string;
  pathname: string;
  url: string;
  postData: string | null;
  /** Request Content-Type header when present (form-encoded login, etc.). */
  contentType: string | null;
}

export interface ApiMock {
  /** `METHOD /pathname` keys that had no handler. */
  unhandled: string[];
  /** Every intercepted `/api/**` request, in order. */
  calls: RecordedCall[];
  /** First recorded call matching `METHOD /pathname`, if any. */
  find: (key: string) => RecordedCall | undefined;
}

export interface AuthEntryOptions {
  /** OIDC provider display names rendered on the login card. */
  oauthProviders?: string[];
  /** When true, the signup link is shown. Defaults to false. */
  allowRegistration?: boolean;
  /**
   * Override the authenticated `/users/me` shell baseline before first
   * navigation. Pass `unauthorizedMe` for login-entry specs that must not look
   * authenticated if something accidentally calls `/users/me`.
   */
  me?: ApiHandler;
}

/** 401 `/users/me` — use via `authEntryRoutes({ me: unauthorizedMe })`. */
export const unauthorizedMe: ApiHandler = () => ({
  status: 401,
  body: { detail: "Unauthorized" },
});

/**
 * Unauthenticated entry routes merged over the authenticated shell.
 *
 * Does not weaken shell defaults: callers opt in per-spec. Auth metadata is
 * always registered; `/users/me` is only replaced when `me` is provided.
 */
export function authEntryRoutes(options: AuthEntryOptions = {}): Record<string, ApiHandler> {
  const routes: Record<string, ApiHandler> = {
    "GET /api/v1/auth/metadata": () => ({
      body: {
        oauth_providers: options.oauthProviders ?? [],
        allow_registration: options.allowRegistration ?? false,
      },
    }),
  };
  if (options.me) {
    routes["GET /api/v1/users/me"] = options.me;
  }
  return routes;
}

// Baseline shell endpoints the authenticated dashboard mounts before any
// page-specific data. A verified superuser satisfies the AuthGate; the rest
// keep the sidebar / providers from erroring.
function shellRoutes(): Record<string, ApiHandler> {
  return {
    "GET /api/v1/users/me": () => ({
      body: {
        id: "00000000-0000-0000-0000-000000000001",
        email: "smoke@example.com",
        is_active: true,
        is_superuser: true,
        is_verified: true,
        last_login_at: null,
      },
    }),
    "GET /api/v1/dashboard/summary": () => ({ body: {} }),
    "GET /api/v1/features": () => ({
      body: { requests: false, subtitles: false, notifications: true },
    }),
    "GET /api/v1/system/version": () => ({ body: { version: "smoke-test" } }),
    "GET /api/v1/system/updates": () => ({ body: { enabled: false } }),
    // web-vitals beacon fired on load; accept and ignore.
    "POST /api/v1/analytics/vitals": () => ({ status: 204 }),
    // The live event stream: answer with an empty, closed SSE response so the
    // EventSource opens without hanging the route handler.
    "GET /api/v1/events/stream": () => ({
      contentType: "text/event-stream",
      body: "",
    }),
  };
}

/**
 * Install `/api/**` interception on `page`. `routes` is merged over the shell
 * baseline (page-specific keys win). Call before the first navigation.
 */
export async function installApiMock(
  page: Page,
  routes: Record<string, ApiHandler>,
): Promise<ApiMock> {
  const table = { ...shellRoutes(), ...routes };
  const mock: ApiMock = {
    unhandled: [],
    calls: [],
    find: (key) => mock.calls.find((c) => `${c.method} ${c.pathname}` === key),
  };

  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const pathname = new URL(req.url()).pathname;
    const key = `${req.method()} ${pathname}`;
    mock.calls.push({
      method: req.method(),
      pathname,
      url: req.url(),
      postData: req.postData(),
      contentType: req.headers()["content-type"] ?? null,
    });

    const handler = table[key];
    if (!handler) {
      mock.unhandled.push(key);
      await route.fulfill({
        status: 501,
        contentType: "application/json",
        body: JSON.stringify({ detail: `unhandled ${key}` }),
      });
      return;
    }

    const spec = await handler(req);
    const isString = typeof spec.body === "string";
    await route.fulfill({
      status: spec.status ?? 200,
      contentType: spec.contentType ?? "application/json",
      headers: spec.headers,
      body:
        spec.body === undefined ? "" : isString ? (spec.body as string) : JSON.stringify(spec.body),
    });
  });

  return mock;
}

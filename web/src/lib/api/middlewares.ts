import type { Middleware } from "openapi-fetch";

// Keep the logout handler on `globalThis` so HMR-driven module
// re-evaluation in dev doesn't reset it back to the noop until the
// dashboard layout's effect re-runs.
const HANDLER_KEY = Symbol.for("mm.api.logoutHandler");
type HandlerHolder = { [HANDLER_KEY]?: () => Promise<void> | void };
const holder = globalThis as unknown as HandlerHolder;
if (!holder[HANDLER_KEY]) holder[HANDLER_KEY] = () => {};

export function registerLogoutHandler(fn: () => Promise<void> | void) {
  holder[HANDLER_KEY] = fn;
}

export const loggingMiddleware: Middleware = {
  async onRequest({ request }) {
    if (process.env.NODE_ENV !== "production") {
      console.log(`Requesting ${request.method} ${request.url}`);
    }
    return request;
  },
  async onResponse({ request, response }) {
    if (!response.ok) {
      console.error(`Request to ${request.url} failed with status ${response.status}`);
    } else if (process.env.NODE_ENV !== "production") {
      console.log(`Request to ${request.url} succeeded with status ${response.status}`);
    }
    return response;
  },
  async onError({ request, error }) {
    console.error(`Fetch to ${request.url} failed:`, error);
    return new Error(
      `Fetch to ${request.url} failed: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    );
  },
};

export const autoLogoutMiddleware: Middleware = {
  async onResponse({ request, response }) {
    if (response.status === 401 && !request.url.endsWith("/auth/cookie/logout")) {
      console.log(`Request to ${request.url} returned 401, logging out...`);
      await holder[HANDLER_KEY]!();
    }
    if (response.status === 403) {
      console.log(`Request to ${request.url} returned 403; consider opening a bug report.`);
    }
    return response;
  },
};

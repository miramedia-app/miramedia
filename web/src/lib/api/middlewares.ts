import type { Middleware } from "openapi-fetch";

import { authCoordinator } from "@/lib/auth-generation";
import type { AuthGeneration, UnauthorizedHandler } from "@/lib/auth-generation";

export function registerLogoutHandler(fn: UnauthorizedHandler) {
  authCoordinator.setUnauthorizedHandler(fn);
}

// The generation each outgoing request was sent under. A 401 is only allowed to
// trigger an auth exit if its request's generation is still current, so a slow
// request from a previous session cannot log out the account that replaced it.
const requestGeneration = new WeakMap<Request, AuthGeneration>();

export const loggingMiddleware: Middleware = {
  async onRequest({ request }) {
    requestGeneration.set(request, authCoordinator.current());
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
    // An aborted request is not a failure: TanStack cancels in-flight queries at
    // auth boundaries (and on unmount) via the `signal` it passes to fetch.
    // Rewrapping the AbortError hides the abort from TanStack, which then treats
    // the cancellation as a real error and retries it.
    if (error instanceof Error && (request.signal?.aborted || error.name === "AbortError")) {
      return error;
    }
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
      // Requests that predate the middleware (none today) are treated as current.
      const token = requestGeneration.get(request) ?? authCoordinator.current();
      console.log(`Request to ${request.url} returned 401 (auth generation ${token})`);
      // The coordinator decides whether this 401 still speaks for the live
      // session: concurrent 401s collapse into one exit, and a 401 answering a
      // request from a previous session is ignored.
      await authCoordinator.reportUnauthorized(token);
    }
    if (response.status === 403) {
      console.log(`Request to ${request.url} returned 403; consider opening a bug report.`);
    }
    return response;
  },
};

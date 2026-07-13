"use client";

import type { QueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { authCoordinator } from "@/lib/auth-generation";

/**
 * Drop every cached response at an auth boundary.
 *
 * The root QueryClient outlives a session: it is created once for the SPA and
 * survives logout/login. Without this reset the previous user's `users/me` stays
 * warm (5m `staleTime`), so the next account to authenticate in the same tab
 * briefly inherits the old identity — including `is_superuser` — and can trip
 * privileged, superuser-only requests.
 *
 * Every auth transition must call this: explicit logout, an automatic 401 /
 * session-expiry redirect, and a successful credential login. Cancel first so an
 * in-flight privileged request cannot resolve into the cache after the clear.
 */
export async function resetAuthCache(queryClient: QueryClient) {
  try {
    // Queries pass TanStack's `signal` through to fetch, so this aborts transport
    // where supported. Cancellation itself must never block the clear.
    await queryClient.cancelQueries();
  } finally {
    queryClient.clear();
  }
}

/**
 * Start an auth transition: invalidate any in-flight 401 exit, wait for it to
 * finish, then drop the previous account's cached state.
 *
 * Returns the new generation. Callers navigate only after awaiting this, so an
 * older unauthorized handler can neither clear the incoming session's cache nor
 * beat it in router order.
 */
export async function beginAuthTransition(queryClient: QueryClient) {
  const token = await authCoordinator.beginTransition();
  await resetAuthCache(queryClient);
  return token;
}

/**
 * Log out, then reset shared state before leaving the page.
 *
 * Nested-finally: neither a failed logout POST (offline, proxy error) nor a
 * failed cancellation may skip the cache clear or the redirect — a half-exited
 * session is exactly when a stale identity is most dangerous.
 */
export async function handleLogout(queryClient: QueryClient, redirect: (path: string) => void) {
  try {
    await apiClient.POST("/api/v1/auth/cookie/logout");
  } finally {
    try {
      await beginAuthTransition(queryClient);
    } finally {
      redirect("/login");
    }
  }
}

/**
 * OAuth is an auth boundary too: it hands the tab to the provider and the
 * browser may restore this page from the bfcache on the way back. Reset before
 * assigning `location.href`, or a restored page can still be holding the
 * previous account's identity and privileged data.
 */
export async function handleOauth(queryClient: QueryClient, toastError: (msg: string) => void) {
  const { error, data } = await apiClient.GET("/api/v1/auth/oauth/authorize", {
    params: {
      query: {
        scopes: ["openid", "email", "profile"],
      },
    },
  });
  if (!error && data?.authorization_url) {
    await beginAuthTransition(queryClient);
    window.location.href = data.authorization_url;
  } else {
    toastError("Failed to initiate OAuth login.");
  }
}

"use client";

import type { QueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";

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
  await queryClient.cancelQueries();
  queryClient.clear();
}

/**
 * Log out, then reset shared state before leaving the page.
 *
 * The reset runs in a `finally` path: a logout request that throws (offline,
 * proxy error) is exactly when we least want the previous identity left behind,
 * so the cache is cleared whether or not the call succeeded.
 */
export async function handleLogout(queryClient: QueryClient, redirect: (path: string) => void) {
  try {
    await apiClient.POST("/api/v1/auth/cookie/logout");
  } finally {
    await resetAuthCache(queryClient);
    redirect("/login");
  }
}

export async function handleOauth(toastError: (msg: string) => void) {
  const { error, data } = await apiClient.GET("/api/v1/auth/oauth/authorize", {
    params: {
      query: {
        scopes: ["openid", "email", "profile"],
      },
    },
  });
  if (!error && data?.authorization_url) {
    window.location.href = data.authorization_url;
  } else {
    toastError("Failed to initiate OAuth login.");
  }
}

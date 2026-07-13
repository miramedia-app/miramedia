"use client";

import type { QueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";

/**
 * Log out and drop every cached response before leaving the page.
 *
 * The root QueryClient outlives a session: it is created once for the SPA and
 * survives logout/login. Without this reset the previous user's `users/me` stays
 * warm (5m `staleTime`), so the next account to log in in the same tab briefly
 * inherits the old identity — including `is_superuser` — and can trip
 * privileged, superuser-only requests. Clear unconditionally: if the logout call
 * itself failed we still want no stale identity left behind.
 */
export async function handleLogout(queryClient: QueryClient, redirect: (path: string) => void) {
  await apiClient.POST("/api/v1/auth/cookie/logout");
  queryClient.clear();
  redirect("/login");
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

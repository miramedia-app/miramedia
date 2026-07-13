"use client";

import type { QueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { authCoordinator, authTransition } from "@/lib/auth-generation";

/**
 * Drop every cached response at an auth boundary.
 *
 * The root QueryClient outlives a session: it is created once for the SPA and
 * survives logout/login. Without this reset the previous user's `users/me` stays
 * warm (5m `staleTime`), so the next account to authenticate in the same tab
 * briefly inherits the old identity — including `is_superuser` — and can trip
 * privileged, superuser-only requests.
 *
 * Cancel first: queries pass TanStack's `signal` through to fetch, so this aborts
 * in transport. Cancellation must never block the clear, hence the `finally`.
 *
 * NOTE: `clear()` empties the cache but does NOT blank the results already held
 * by mounted QueryObservers — see `beginAuthTransition`, which blanks the tree
 * first. Never call this on its own at an auth boundary.
 */
export async function resetAuthCache(queryClient: QueryClient) {
  try {
    await queryClient.cancelQueries();
  } finally {
    queryClient.clear();
  }
}

/**
 * Begin an auth transition: blank the authenticated tree, advance the auth
 * generation, drain any in-flight 401 exit, then drop the previous account's
 * cached state.
 *
 * Order matters. `authTransition.begin()` comes first and is never undone: a
 * mounted `useQuery` keeps serving its last observed result even after
 * `clear()`, so if navigation then throws or stalls, `UserProvider` would still
 * report the old `is_superuser: true` and the privileged UI would stay painted.
 * Blanking is what actually makes the old identity unobservable; the cache clear
 * and the navigation are follow-through.
 */
export async function beginAuthTransition(queryClient: QueryClient) {
  authTransition.begin();
  const token = await authCoordinator.beginTransition();
  await resetAuthCache(queryClient);
  return token;
}

/**
 * Leave the SPA via a full document load — never an SPA navigation.
 *
 * A client-side `router.push` keeps the JS context, and with it every live
 * QueryObserver from the dead session. Replacing the document is the only thing
 * that guarantees none survives, so all three attempts here are full-document:
 * `replace` first (the dead session must not be reachable via Back), then
 * `assign`, then `href`.
 *
 * If every full-document mechanism throws, we deliberately do NOT fall back to
 * the router. The tree stays blank — `authTransition` is never reset — which is
 * the safe end state: no identity, no privileged UI, no navigation that would
 * quietly keep the old observers alive.
 *
 * @returns true if a full-document navigation was initiated.
 */
export function hardNavigate(path: string): boolean {
  const attempts: ((p: string) => void)[] = [
    (p) => window.location.replace(p),
    (p) => window.location.assign(p),
    (p) => {
      window.location.href = p;
    },
  ];
  for (const attempt of attempts) {
    try {
      attempt(path);
      return true;
    } catch {
      // Try the next full-document mechanism.
    }
  }
  return false;
}

/**
 * Log out: blank and clear FIRST, then post, then leave via a full document load.
 *
 * The transition runs before the POST, not after: a stalled logout request would
 * otherwise leave the old authenticated (possibly admin) UI painted and
 * interactive for as long as the request hangs. Transitioning first also advances
 * the auth generation before the request, so a 401 racing it cannot open a second
 * exit.
 *
 * `finally` — a logout POST that throws (offline, proxy error) must still leave.
 * The session is already blanked and cleared by then.
 */
export async function handleLogout(queryClient: QueryClient) {
  try {
    await beginAuthTransition(queryClient);
    await apiClient.POST("/api/v1/auth/cookie/logout");
  } finally {
    hardNavigate("/login");
  }
}

/**
 * OAuth is an auth boundary too: it hands the tab to the provider, and the
 * browser may restore this page from the bfcache on the way back. Transition
 * before assigning `location.href`, or a restored page can still hold — and
 * render — the previous account's identity and privileged data.
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

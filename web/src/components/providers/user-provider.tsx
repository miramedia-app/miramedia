"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { authTransition } from "@/lib/auth-generation";
import type { components } from "@/lib/api/api";

type User = components["schemas"]["UserRead"];

type UserContextValue = {
  user: User | null;
  isLoading: boolean;
  refresh: () => void;
};

const UserContext = React.createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const query = useQuery({
    queryKey: ["users", "me"],
    // Pass TanStack's signal through to fetch so `cancelQueries` at an auth
    // boundary aborts the request in transport, not just in the cache.
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/users/me", { signal });
      if (error) throw error;
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });

  // Prefetch dashboard counts as soon as auth succeeds so /dashboard avoids a
  // second waterfall (static export cannot forward cookies for RSC fetches).
  useQuery({
    queryKey: ["dashboard", "summary"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/dashboard/summary", { signal });
      if (error) throw error;
      return data;
    },
    enabled: !!query.data,
    staleTime: 30 * 1000,
  });

  // `QueryClient.clear()` does not blank an active observer's last result, so a
  // stalled or failed navigation could leave this provider still handing out the
  // previous account's `is_superuser`. Once an auth transition starts we report
  // no user, unconditionally, until a new document initializes.
  const isTransitioning = React.useSyncExternalStore(
    authTransition.subscribe,
    authTransition.isTransitioning,
    () => false,
  );

  const { data, isLoading, refetch } = query;
  const value = React.useMemo<UserContextValue>(
    () => ({
      user: isTransitioning ? null : (data ?? null),
      isLoading: isTransitioning || isLoading,
      refresh: () => void refetch(),
    }),
    [data, isLoading, refetch, isTransitioning],
  );

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useUser(): UserContextValue {
  const ctx = React.useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
}

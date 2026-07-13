"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
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

  const { data, isLoading, refetch } = query;
  const value = React.useMemo<UserContextValue>(
    () => ({
      user: data ?? null,
      isLoading,
      refresh: () => void refetch(),
    }),
    [data, isLoading, refetch],
  );

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useUser(): UserContextValue {
  const ctx = React.useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
}

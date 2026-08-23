"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

export type Features = components["schemas"]["FeatureFlags"];
export type FeaturesContextValue = Features & { ready: boolean };

export const DEFAULT_FEATURES: Features = {
  requests: false,
  subtitles: false,
  notifications: true,
  watchlists: true,
  custom_lists: true,
  watch_next: true,
  watch_next_include_specials: false,
  upcoming: true,
  upcoming_default_past_days: 0,
  upcoming_default_future_days: 30,
  continue_watching: false,
  streaming: true,
  downloads: true,
};

const FEATURES_UNAVAILABLE: Features = {
  requests: false,
  subtitles: false,
  notifications: false,
  watchlists: false,
  custom_lists: false,
  watch_next: false,
  watch_next_include_specials: false,
  upcoming: false,
  upcoming_default_past_days: 0,
  upcoming_default_future_days: 30,
  continue_watching: false,
  streaming: false,
  downloads: false,
};

type FeaturesStatusValue = {
  features: Features;
  isPending: boolean;
  isError: boolean;
};

const FeaturesContext = React.createContext<FeaturesStatusValue>({
  features: DEFAULT_FEATURES,
  isPending: false,
  isError: false,
});

export async function fetchFeatures(signal?: AbortSignal): Promise<Features> {
  const { data, error } = await apiClient.GET("/api/v1/features", { signal });
  if (error) throw error;
  return data;
}

export function resolveFeatures(state: { data: Features | undefined; isError: boolean }): Features {
  if (state.data) return state.data;
  if (state.isError) return FEATURES_UNAVAILABLE;
  return DEFAULT_FEATURES;
}

export function featuresReady(isPending: boolean, isError: boolean): boolean {
  return !isPending && !isError;
}

export function FeaturesProvider({ children }: { children: React.ReactNode }) {
  const { data, isPending, isError } = useQuery({
    queryKey: ["features"],
    queryFn: ({ signal }) => fetchFeatures(signal),
    staleTime: 10 * 60 * 1000,
  });
  const value = React.useMemo(
    () => ({ features: resolveFeatures({ data, isError }), isPending, isError }),
    [data, isPending, isError],
  );
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export function useFeatures(): FeaturesContextValue {
  const { features, isPending, isError } = React.useContext(FeaturesContext);
  return { ...features, ready: featuresReady(isPending, isError) };
}

export function useFeaturesStatus(): { isPending: boolean; isError: boolean } {
  const { isPending, isError } = React.useContext(FeaturesContext);
  return { isPending, isError };
}

"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";

type Features = {
  requests: boolean;
  subtitles: boolean;
  notifications: boolean;
};

const DEFAULT_FEATURES: Features = {
  requests: false,
  subtitles: false,
  notifications: true,
};

const FeaturesContext = React.createContext<Features>(DEFAULT_FEATURES);

export function FeaturesProvider({ children }: { children: React.ReactNode }) {
  const { data } = useQuery({
    queryKey: ["features"],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/features");
      return (data ?? DEFAULT_FEATURES) as Features;
    },
    staleTime: 10 * 60 * 1000,
  });

  return (
    <FeaturesContext.Provider value={(data as Features) ?? DEFAULT_FEATURES}>
      {children}
    </FeaturesContext.Provider>
  );
}

export function useFeatures() {
  return React.useContext(FeaturesContext);
}

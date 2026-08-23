"use client";

import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

export type QualityOption = components["schemas"]["QualityOptionSchema"];
export type CodecOption = components["schemas"]["CodecOptionSchema"];

type QualityCodecData = {
  qualityOptions: QualityOption[];
  codecOptions: CodecOption[];
};

export function useQualityCodecOptions() {
  return useQuery({
    queryKey: ["system", "settings"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/system/settings", {
        signal,
      });
      if (error) throw error;
      return data;
    },
    staleTime: 5 * 60 * 1000,
    select: (data): QualityCodecData => ({
      qualityOptions: (data?.indexers?.quality_options as QualityOption[]) ?? [],
      codecOptions: (data?.indexers?.codec_options as CodecOption[]) ?? [],
    }),
  });
}

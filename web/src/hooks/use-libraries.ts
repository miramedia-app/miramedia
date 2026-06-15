"use client";

import { useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

export type LibraryItem = components["schemas"]["LibraryItem"];
export type MediaKind = "show" | "movie";

export function useLibraries(mediaType: MediaKind) {
  return useQuery({
    queryKey: ["libraries", mediaType],
    queryFn: async () => {
      const path = mediaType === "show" ? "/api/v1/shows/libraries" : "/api/v1/movies/libraries";
      const { data } = await apiClient.GET(path);
      return (data ?? []) as LibraryItem[];
    },
    staleTime: 5 * 60 * 1000,
  });
}

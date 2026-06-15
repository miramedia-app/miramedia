// Shared React Query options for media detail pages.
//
// Why this exists: the show/movie detail pages and the hover-prefetch logic
// must agree on query key AND queryFn, or a prefetch warms a cache the detail
// page never reads. Co-locating them here keeps the two in lockstep.

import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type Show = components["schemas"]["PublicShow"];

export type ShowDetailBundle = components["schemas"]["ShowDetailBundle"];
export type MovieDetailBundle = components["schemas"]["MovieDetailBundle"];

/** Sort seasons (and their episodes) once, so the detail render path never
 * re-sorts on every parent render. Mirrors what the detail queryFn expects. */
export function sortShowSeasons(show: Show): Show {
  const seasons = [...(show.seasons ?? [])]
    .map((s) => ({
      ...s,
      episodes: [...(s.episodes ?? [])].sort((a, b) => a.number - b.number),
    }))
    .sort((a, b) => a.number - b.number);
  return { ...show, seasons };
}

export function showDetailBundleQueryOptions(showId: string) {
  return {
    queryKey: ["show", showId, "bundle"] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/shows/{show_id}/detail-bundle", {
        params: { path: { show_id: showId } },
      });
      if (error) throw error;
      if (!data) throw new Error("Missing show detail bundle");
      return {
        ...data,
        show: sortShowSeasons(data.show),
      };
    },
  };
}

export function movieDetailBundleQueryOptions(movieId: string) {
  return {
    queryKey: ["movie", movieId, "bundle"] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/movies/{movie_id}/detail-bundle", {
        params: { path: { movie_id: movieId } },
      });
      if (error) throw error;
      if (!data) throw new Error("Missing movie detail bundle");
      return data;
    },
  };
}

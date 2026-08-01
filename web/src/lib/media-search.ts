import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

export type MediaSearchResult = components["schemas"]["MetaDataProviderSearchResult"];

export type MediaSearchType = "show" | "movie";

/** Suggestion rows the search dropdown will render at most. */
export const MEDIA_SEARCH_LIMIT = 8;

/**
 * Fetch search suggestions for the dropdown.
 *
 * `openapi-fetch` never throws on 4xx/5xx, so a metadata-provider outage would
 * otherwise resolve to `[]` and render as an innocent "no results". Throwing
 * lets React Query surface the failure so the dropdown can say so.
 */
export async function fetchMediaSuggestions(
  mediaType: MediaSearchType,
  query: string,
  signal?: AbortSignal,
  client: typeof apiClient = apiClient,
): Promise<MediaSearchResult[]> {
  const { data, error } =
    mediaType === "show"
      ? await client.GET("/api/v1/shows/search", { signal, params: { query: { query } } })
      : await client.GET("/api/v1/movies/search", { signal, params: { query: { query } } });
  if (error) throw new Error("Search unavailable");
  return (data ?? []).slice(0, MEDIA_SEARCH_LIMIT);
}

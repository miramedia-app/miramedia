import { describe, expect, it, vi } from "vitest";

import apiClient from "@/lib/api/client";
import { MEDIA_SEARCH_LIMIT, fetchMediaSuggestions } from "@/lib/media-search";

/** Minimal stand-in for the openapi-fetch client: only `GET` is exercised. */
function fakeClient(result: unknown) {
  const get = vi.fn().mockResolvedValue(result);
  return { client: { GET: get } as unknown as typeof apiClient, get };
}

describe("fetchMediaSuggestions", () => {
  it("throws instead of resolving to an empty list when the API errors", async () => {
    const { client } = fakeClient({ data: undefined, error: { detail: "provider down" } });

    await expect(fetchMediaSuggestions("show", "dune", undefined, client)).rejects.toThrow(
      "Search unavailable",
    );
  });

  it("throws even when the API returns both an error and a body", async () => {
    const { client } = fakeClient({ data: [], error: { detail: "partial outage" } });

    await expect(fetchMediaSuggestions("movie", "dune", undefined, client)).rejects.toThrow(
      "Search unavailable",
    );
  });

  it("returns results capped at the dropdown limit", async () => {
    const many = Array.from({ length: MEDIA_SEARCH_LIMIT + 5 }, (_, i) => ({ external_id: i }));
    const { client } = fakeClient({ data: many, error: undefined });

    const results = await fetchMediaSuggestions("show", "dune", undefined, client);

    expect(results).toHaveLength(MEDIA_SEARCH_LIMIT);
  });

  it("tolerates a missing body", async () => {
    const { client } = fakeClient({ data: undefined, error: undefined });

    await expect(fetchMediaSuggestions("show", "dune", undefined, client)).resolves.toEqual([]);
  });

  it("queries the endpoint matching the media type and forwards the signal", async () => {
    const signal = new AbortController().signal;
    const { client, get } = fakeClient({ data: [], error: undefined });

    await fetchMediaSuggestions("movie", "dune", signal, client);

    expect(get).toHaveBeenCalledWith("/api/v1/movies/search", {
      signal,
      params: { query: { query: "dune" } },
    });
  });
});

"use client";

import * as React from "react";
import { toast } from "sonner";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { MediaSearchInput } from "@/components/media-search-input";
import type { components } from "@/lib/api/api";

type SearchResult = components["schemas"]["MetaDataProviderSearchResult"];

const PAGE_SIZE = 10;

function handleQueryNotificationToast(count: number, query: string) {
  if (count > 0 && query.length > 0) {
    toast.success(`Found ${count} ${count > 1 ? "results" : "result"} for search term "${query}".`);
  } else if (count === 0 && query.length > 0) {
    toast.info(`No results found for "${query}".`);
  }
}

async function fetchMedia(
  mediaType: "show" | "movie",
  query: string,
  skip: number,
): Promise<SearchResult[]> {
  // Provider precedence (TMDB → TVDB → Cinemeta → TVMaze) is resolved
  // server-side; the client no longer pins a single provider.
  const endpoint =
    mediaType === "show"
      ? query.length > 0
        ? "/api/v1/shows/search"
        : "/api/v1/shows/recommended"
      : query.length > 0
        ? "/api/v1/movies/search"
        : "/api/v1/movies/recommended";

  const params = query.length > 0 ? { query: { query } } : { query: { skip } };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { data, error } = await (apiClient as any).GET(endpoint, { params });
  if (error) throw new Error("Metadata provider request failed");
  return (data ?? []) as SearchResult[];
}

const PROVIDER_ERROR =
  "Unable to reach the metadata provider. Check your configuration and try again.";

export function AddMediaSearch({
  mediaType = "show",
  onResultsChange,
  onLoadingChange,
  onHasMoreChange,
  onErrorMessageChange,
}: {
  mediaType?: "show" | "movie";
  results: SearchResult[] | null;
  onResultsChange: (v: SearchResult[] | null) => void;
  isLoading: boolean;
  onLoadingChange: (v: boolean) => void;
  hasMore: boolean;
  onHasMoreChange: (v: boolean) => void;
  errorMessage: string | null;
  onErrorMessageChange: (v: string | null) => void;
}) {
  const [searchTerm, setSearchTerm] = React.useState("");
  const [submittedQuery, setSubmittedQuery] = React.useState("");

  const mediaLabel = mediaType === "show" ? "Show" : "Movie";
  const browseMode = submittedQuery.length === 0;

  // Browse mode = paginated "recommended". useInfiniteQuery dedups the
  // StrictMode double-mount and caches pages across back/forward navigation.
  const browseQuery = useInfiniteQuery({
    queryKey: ["media-browse", mediaType],
    queryFn: ({ pageParam }) => fetchMedia(mediaType, "", pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length < PAGE_SIZE ? undefined : allPages.reduce((n, p) => n + p.length, 0),
    enabled: browseMode,
    staleTime: 60 * 1000,
    // The `added`/`id` flags reflect current library membership, recomputed
    // server-side per request. Re-fetch whenever the page is (re)opened so a
    // show added/imported elsewhere shows "View" instead of a stale "Add".
    refetchOnMount: "always",
  });

  // Search mode = single (non-paginated) lookup keyed on the submitted query.
  const searchQuery = useQuery({
    queryKey: ["media-search", mediaType, submittedQuery],
    queryFn: () => fetchMedia(mediaType, submittedQuery, 0),
    enabled: !browseMode,
    staleTime: 60 * 1000,
    refetchOnMount: "always",
  });

  // Flatten browse pages and dedup by external_id (provider paging can repeat).
  const browseResults = React.useMemo(() => {
    const seen = new Set<SearchResult["external_id"]>();
    const out: SearchResult[] = [];
    for (const page of browseQuery.data?.pages ?? []) {
      for (const r of page) {
        if (seen.has(r.external_id)) continue;
        seen.add(r.external_id);
        out.push(r);
      }
    }
    return out;
  }, [browseQuery.data]);

  const results = React.useMemo(
    () => (browseMode ? browseResults : (searchQuery.data ?? [])),
    [browseMode, browseResults, searchQuery.data],
  );
  const isFetching = browseMode ? browseQuery.isFetching : searchQuery.isFetching;
  const isError = browseMode ? browseQuery.isError : searchQuery.isError;
  const hasMore = browseMode ? (browseQuery.hasNextPage ?? false) : false;

  // Sync derived state up to the parent, which owns the results grid render.
  React.useEffect(() => {
    onResultsChange(results.length > 0 ? results : null);
  }, [results, onResultsChange]);
  React.useEffect(() => {
    onLoadingChange(isFetching);
  }, [isFetching, onLoadingChange]);
  React.useEffect(() => {
    onHasMoreChange(hasMore);
  }, [hasMore, onHasMoreChange]);
  React.useEffect(() => {
    onErrorMessageChange(isError ? PROVIDER_ERROR : null);
  }, [isError, onErrorMessageChange]);

  // Toast once per successful search (not per cache hit / refetch).
  const toastedFor = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (browseMode) {
      toastedFor.current = null;
      return;
    }
    if (searchQuery.isSuccess && toastedFor.current !== submittedQuery) {
      toastedFor.current = submittedQuery;
      handleQueryNotificationToast(searchQuery.data?.length ?? 0, submittedQuery);
    }
  }, [browseMode, searchQuery.isSuccess, searchQuery.data, submittedQuery]);

  const loadMore = React.useCallback(() => {
    if (browseMode && browseQuery.hasNextPage && !browseQuery.isFetchingNextPage) {
      void browseQuery.fetchNextPage();
    }
  }, [browseMode, browseQuery]);

  // Attach the scroll listener once; the handler reads latest values via ref
  // so re-rendering for search-state changes doesn't re-bind it.
  const scrollStateRef = React.useRef({ browseMode, hasMore, isFetching, loadMore });
  scrollStateRef.current = { browseMode, hasMore, isFetching, loadMore };
  React.useEffect(() => {
    function handleScroll() {
      const s = scrollStateRef.current;
      if (!s.browseMode || !s.hasMore || s.isFetching) return;
      const scrollBottom = window.innerHeight + window.scrollY;
      const docHeight = document.documentElement.scrollHeight;
      if (docHeight - scrollBottom < 400) s.loadMore();
    }
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  function search(query: string) {
    setSubmittedQuery(query);
  }

  function handleSuggestionSelect(result: SearchResult) {
    const q = result.name + (result.year != null ? ` (${result.year})` : "");
    setSearchTerm(q);
    setSubmittedQuery(result.name);
  }

  return (
    <div className="w-full">
      <MediaSearchInput
        value={searchTerm}
        onValueChange={setSearchTerm}
        mediaType={mediaType}
        placeholder={`Search for a ${mediaLabel.toLowerCase()}…`}
        onSelect={handleSuggestionSelect}
        onSubmit={(q) => search(q)}
        onSubmitClick={() => search(searchTerm)}
        submitLoading={isFetching}
      />
    </div>
  );
}

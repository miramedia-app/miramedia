"use client";

import * as React from "react";
import Link from "next/link";
import { Tv, TriangleAlert } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { DashboardHeader } from "@/components/dashboard-header";
import { MediaPicture } from "@/components/media-picture";
import { DownloadedBadge } from "@/components/downloaded-badge";
import { DataListEmpty } from "@/components/data-list";
import { MediaGridSkeleton } from "@/components/media-grid-skeleton";
import { MediaGridControls } from "@/components/media-grid-controls";
import type { ActiveFilter, FacetDef } from "@/components/data-list";
import { MediaPagination } from "@/components/media-pagination";
import { VirtualMediaGrid } from "@/components/virtual-media-grid";
import apiClient from "@/lib/api/client";
import { useShowPrefetch } from "@/lib/use-media-prefetch";
import type { components, paths } from "@/lib/api/api";

type Show = components["schemas"]["PublicShow"] & {
  vote_average?: number | null;
};
type ShowsQuery = NonNullable<paths["/api/v1/shows"]["get"]["parameters"]["query"]>;

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];

const STAR_ICON = (
  <svg className="mr-1 h-3.5 w-3.5 text-yellow-400" fill="currentColor" viewBox="0 0 20 20">
    <path d="M10 15l-5.878 3.09 1.122-6.545L.488 6.91l6.561-.955L10 0l2.951 5.955 6.561.955-4.756 4.635 1.122 6.545z" />
  </svg>
);

/** Download progress across all non-skipped (wanted) episodes. Skipped
 * episodes are ignored. `complete` requires >=1 wanted episode all done.
 * Memoized per-show via WeakMap — same Show identity reuses last result. */
type Progress = { downloaded: number; total: number; complete: boolean };
const progressCache = new WeakMap<Show, Progress>();
function showDownloadProgress(show: Show): Progress {
  const cached = progressCache.get(show);
  if (cached) return cached;
  let downloaded = 0;
  let total = 0;
  // The list endpoint ships aggregate counts (no episode tree). Fall back to
  // iterating the tree when present (detail-seeded data, or older API).
  if (
    typeof show.downloaded_episode_count === "number" &&
    typeof show.wanted_episode_count === "number"
  ) {
    downloaded = show.downloaded_episode_count;
    total = show.wanted_episode_count;
  } else {
    for (const season of show.seasons ?? []) {
      for (const ep of season.episodes ?? []) {
        if (ep.skipped) continue;
        total++;
        if (ep.downloaded) downloaded++;
      }
    }
  }
  const result: Progress = {
    downloaded,
    total,
    complete: total > 0 && downloaded === total,
  };
  progressCache.set(show, result);
  return result;
}

export default function ShowsPage() {
  const prefetchShow = useShowPrefetch();
  const [searchQuery, setSearchQuery] = React.useState("");
  const deferredSearchQuery = React.useDeferredValue(searchQuery);
  const [sortBy, setSortBy] = React.useState("name-asc");
  const [filters, setFiltersState] = React.useState<ActiveFilter[]>([]);
  const [pageSize, setPageSizeState] = React.useState(50);
  const [currentPage, setCurrentPage] = React.useState(1);

  // Page-reset deps belong inside the setters that change them, not in a
  // mirroring effect over JSON.stringify(filters).
  const setFilters = React.useCallback((next: ActiveFilter[]) => {
    setFiltersState(next);
    setCurrentPage(1);
  }, []);
  const setPageSize = React.useCallback((next: number) => {
    setPageSizeState(next);
    setCurrentPage(1);
  }, []);
  const handleSearchChange = React.useCallback((next: string) => {
    setSearchQuery(next);
    setCurrentPage(1);
  }, []);
  const handleSortChange = React.useCallback((next: string) => {
    setSortBy(next);
    setCurrentPage(1);
  }, []);

  const filterParams = React.useMemo(() => {
    const params: Record<string, string[] | number[]> = {};
    for (const f of filters) {
      const excluded = f.operator === "excludes" || f.operator === "is_not";
      const prefix = excluded ? "exclude_" : "";
      if (f.facetId === "status") params[`${prefix}status`] = f.values;
      if (f.facetId === "airing") {
        params[`${prefix}airing`] = f.values;
      }
      if (f.facetId === "library") params[`${prefix}library`] = f.values;
      if (f.facetId === "genre") params[`${prefix}genre`] = f.values;
      if (f.facetId === "decade") {
        params[`${prefix}decade`] = f.values.map((v) => Number(v));
      }
    }
    return params;
  }, [filters]);

  const facetsQuery = useQuery({
    queryKey: ["shows", "facets"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/shows/facets", { signal });
      if (error) throw error;
      return data ?? { libraries: [], genres: [], decades: [] };
    },
    staleTime: 5 * 60 * 1000,
    retry: 0,
  });

  const showsQuery = useQuery({
    queryKey: ["shows", "list", deferredSearchQuery, sortBy, currentPage, pageSize, filterParams],
    queryFn: async ({ signal }) => {
      const baseQuery: ShowsQuery = {
        q: deferredSearchQuery.trim() || undefined,
        sort: sortBy,
        limit: pageSize,
        offset: (currentPage - 1) * pageSize,
        ...(filterParams as Partial<ShowsQuery>),
      };
      const listRes = await apiClient.GET("/api/v1/shows", {
        signal,
        params: { query: baseQuery },
      });
      if (listRes.error) throw listRes.error;
      const fallbackTotal = listRes.data?.length ?? 0;
      const total = Number(listRes.response?.headers?.get("x-total-count") ?? fallbackTotal);
      return {
        items: (listRes.data ?? []) as Show[],
        total,
      };
    },
    placeholderData: (prev) => prev,
    retry: 0,
  });

  const shows = React.useMemo(() => showsQuery.data?.items ?? [], [showsQuery.data]);
  const totalShows = showsQuery.data?.total ?? 0;
  const loadError = showsQuery.isError ? "Failed to load shows" : null;

  const facets = React.useMemo<FacetDef<Show>[]>(() => {
    const libraries = facetsQuery.data?.libraries ?? [];
    const genres = facetsQuery.data?.genres ?? [];
    const decades = facetsQuery.data?.decades ?? [];
    const progressKey = (s: Show) => {
      const p = showDownloadProgress(s);
      if (p.complete) return "complete";
      if (p.downloaded > 0) return "partial";
      return "none";
    };
    return [
      {
        id: "status",
        label: "Status",
        options: [
          { value: "complete", label: "Complete" },
          { value: "partial", label: "In progress" },
          { value: "none", label: "Not started" },
        ],
        predicate: (s, values, op) => {
          const hit = values.includes(progressKey(s));
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "airing",
        label: "Airing",
        options: [
          { value: "continuing", label: "Continuing" },
          { value: "ended", label: "Ended" },
        ],
        predicate: (s, values, op) => {
          const hit = values.includes(s.ended ? "ended" : "continuing");
          return op === "excludes" ? !hit : hit;
        },
      },
      ...(libraries.length > 1
        ? [
            {
              id: "library",
              label: "Library",
              options: libraries.map((l) => ({ value: l, label: l })),
              predicate: (s: Show, values: string[], op) => {
                const hit = values.includes(s.library);
                return op === "excludes" ? !hit : hit;
              },
            } as FacetDef<Show>,
          ]
        : []),
      ...(genres.length > 0
        ? [
            {
              id: "genre",
              label: "Genre",
              options: genres.map((g) => ({ value: g, label: g })),
              predicate: (s: Show, values: string[], op) => {
                const hit = (s.genres ?? []).some((g) => values.includes(g));
                return op === "excludes" ? !hit : hit;
              },
            } as FacetDef<Show>,
          ]
        : []),
      ...(decades.length > 0
        ? [
            {
              id: "decade",
              label: "Decade",
              options: decades.map((d) => ({ value: String(d), label: `${d}s` })),
              predicate: (s: Show, values: string[], op) => {
                const hit = s.year != null && values.includes(String(Math.floor(s.year / 10) * 10));
                return op === "excludes" ? !hit : hit;
              },
            } as FacetDef<Show>,
          ]
        : []),
    ];
  }, [facetsQuery.data]);

  const totalPages = Math.max(1, Math.ceil(totalShows / pageSize));
  React.useEffect(() => {
    if (currentPage > totalPages) setCurrentPage(totalPages);
  }, [currentPage, totalPages]);

  return (
    <>
      <DashboardHeader crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Shows" }]} />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        <MediaGridControls
          searchQuery={searchQuery}
          onSearchChange={handleSearchChange}
          sortBy={sortBy}
          onSortChange={handleSortChange}
          searchPlaceholder="Search or filter shows…"
          addHref="/dashboard/shows/add"
          addLabel="Add Show"
          facets={facets}
          filters={filters}
          onFiltersChange={setFilters}
        />

        {loadError ? (
          <DataListEmpty
            icon={<TriangleAlert />}
            title={loadError}
            description="Check that the metadata provider is configured and reachable."
            action={
              <Button variant="outline" size="sm" onClick={() => showsQuery.refetch()}>
                Retry
              </Button>
            }
          />
        ) : showsQuery.isLoading ? (
          <MediaGridSkeleton />
        ) : (
          <>
            {totalShows === 0 ? (
              !searchQuery && filters.length === 0 ? (
                <DataListEmpty
                  icon={<Tv />}
                  title="No shows yet"
                  description="Add a show to start building your library."
                />
              ) : (
                <DataListEmpty
                  icon={<Tv />}
                  title="No matching shows"
                  description="No shows match your search or filters."
                />
              )
            ) : (
              <VirtualMediaGrid
                items={shows}
                getKey={(show) => show.id ?? ""}
                renderItem={(show) => {
                  const progress = showDownloadProgress(show);
                  return (
                    <div
                      className="flex flex-col"
                      onMouseEnter={() => prefetchShow(show)}
                      onFocus={() => prefetchShow(show)}
                    >
                      <Link href={`/dashboard/shows/${show.id}`} className="group">
                        <div className="relative aspect-[2/3] overflow-hidden rounded-lg">
                          <MediaPicture media={show} />
                          <DownloadedBadge
                            complete={progress.complete}
                            downloaded={progress.downloaded}
                            total={progress.total}
                          />
                        </div>
                      </Link>
                      <div className="flex flex-col gap-1.5 py-2">
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          {show.year != null && <span>{show.year}</span>}
                          {show.vote_average != null && (
                            <span className="ml-auto flex items-center font-medium text-yellow-600">
                              {STAR_ICON}
                              {Math.round(show.vote_average)}/10
                            </span>
                          )}
                        </div>
                        <Link href={`/dashboard/shows/${show.id}`} className="group">
                          <p className="line-clamp-2 min-h-[2.5rem] text-sm leading-tight font-medium group-hover:underline">
                            {show.name}
                          </p>
                        </Link>
                        <Button
                          render={<Link href={`/dashboard/shows/${show.id}`} />}
                          variant="outline"
                          className="w-full border-border bg-clip-border font-semibold"
                        >
                          View
                        </Button>
                      </div>
                    </div>
                  );
                }}
              />
            )}

            <MediaPagination
              page={currentPage}
              totalPages={totalPages}
              onPageChange={setCurrentPage}
              total={totalShows}
              pageSize={pageSize}
              pageSizeOptions={PAGE_SIZE_OPTIONS}
              onPageSizeChange={setPageSize}
            />
          </>
        )}
      </main>
    </>
  );
}

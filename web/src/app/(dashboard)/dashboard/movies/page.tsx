"use client";

import * as React from "react";
import Link from "next/link";
import { Film, TriangleAlert } from "lucide-react";
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
import { useMoviePrefetch } from "@/lib/use-media-prefetch";
import type { components, paths } from "@/lib/api/api";

type Movie = components["schemas"]["PublicMovie"] & {
  vote_average?: number | null;
};
type MoviesQuery = NonNullable<paths["/api/v1/movies"]["get"]["parameters"]["query"]>;

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];

const STAR_ICON = (
  <svg className="mr-1 h-3.5 w-3.5 text-yellow-400" fill="currentColor" viewBox="0 0 20 20">
    <path d="M10 15l-5.878 3.09 1.122-6.545L.488 6.91l6.561-.955L10 0l2.951 5.955 6.561.955-4.756 4.635 1.122 6.545z" />
  </svg>
);

export default function MoviesPage() {
  const prefetchMovie = useMoviePrefetch();
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
      if (f.facetId === "library") params[`${prefix}library`] = f.values;
      if (f.facetId === "genre") params[`${prefix}genre`] = f.values;
      if (f.facetId === "decade") {
        params[`${prefix}decade`] = f.values.map((v) => Number(v));
      }
    }
    return params;
  }, [filters]);

  const facetsQuery = useQuery({
    queryKey: ["movies", "facets"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/movies/facets", { signal });
      if (error) throw error;
      return data ?? { libraries: [], genres: [], decades: [] };
    },
    staleTime: 5 * 60 * 1000,
    retry: 0,
  });

  const moviesQuery = useQuery({
    queryKey: ["movies", "list", deferredSearchQuery, sortBy, currentPage, pageSize, filterParams],
    queryFn: async ({ signal }) => {
      const baseQuery: MoviesQuery = {
        q: deferredSearchQuery.trim() || undefined,
        sort: sortBy,
        limit: pageSize,
        offset: (currentPage - 1) * pageSize,
        ...(filterParams as Partial<MoviesQuery>),
      };
      const listRes = await apiClient.GET("/api/v1/movies", {
        signal,
        params: { query: baseQuery },
      });
      if (listRes.error) throw listRes.error;
      const fallbackTotal = listRes.data?.length ?? 0;
      const total = Number(listRes.response?.headers?.get("x-total-count") ?? fallbackTotal);
      return {
        items: (listRes.data ?? []) as Movie[],
        total,
      };
    },
    placeholderData: (prev) => prev,
    retry: 0,
  });

  const movies = React.useMemo(() => moviesQuery.data?.items ?? [], [moviesQuery.data]);
  const totalMovies = moviesQuery.data?.total ?? 0;
  const loadError = moviesQuery.isError ? "Failed to load movies" : null;

  const facets = React.useMemo<FacetDef<Movie>[]>(() => {
    const libraries = facetsQuery.data?.libraries ?? [];
    const genres = facetsQuery.data?.genres ?? [];
    const decades = facetsQuery.data?.decades ?? [];
    return [
      {
        id: "status",
        label: "Status",
        options: [
          { value: "downloaded", label: "Downloaded" },
          { value: "not_downloaded", label: "Not downloaded" },
        ],
        predicate: (m, values, op) => {
          const key = m.downloaded ? "downloaded" : "not_downloaded";
          const hit = values.includes(key);
          return op === "excludes" ? !hit : hit;
        },
      },
      ...(libraries.length > 1
        ? [
            {
              id: "library",
              label: "Library",
              options: libraries.map((l) => ({ value: l, label: l })),
              predicate: (m: Movie, values: string[], op) => {
                const hit = values.includes(m.library);
                return op === "excludes" ? !hit : hit;
              },
            } as FacetDef<Movie>,
          ]
        : []),
      ...(genres.length > 0
        ? [
            {
              id: "genre",
              label: "Genre",
              options: genres.map((g) => ({ value: g, label: g })),
              predicate: (m: Movie, values: string[], op) => {
                const hit = (m.genres ?? []).some((g) => values.includes(g));
                return op === "excludes" ? !hit : hit;
              },
            } as FacetDef<Movie>,
          ]
        : []),
      ...(decades.length > 0
        ? [
            {
              id: "decade",
              label: "Decade",
              options: decades.map((d) => ({ value: String(d), label: `${d}s` })),
              predicate: (m: Movie, values: string[], op) => {
                const hit = m.year != null && values.includes(String(Math.floor(m.year / 10) * 10));
                return op === "excludes" ? !hit : hit;
              },
            } as FacetDef<Movie>,
          ]
        : []),
    ];
  }, [facetsQuery.data]);

  const totalPages = Math.max(1, Math.ceil(totalMovies / pageSize));
  React.useEffect(() => {
    if (currentPage > totalPages) setCurrentPage(totalPages);
  }, [currentPage, totalPages]);

  return (
    <>
      <DashboardHeader crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Movies" }]} />
      <main className="flex w-full flex-1 flex-col gap-4 p-4 pt-0">
        <MediaGridControls
          searchQuery={searchQuery}
          onSearchChange={handleSearchChange}
          sortBy={sortBy}
          onSortChange={handleSortChange}
          searchPlaceholder="Search or filter movies…"
          addHref="/dashboard/movies/add"
          addLabel="Add Movie"
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
              <Button variant="outline" size="sm" onClick={() => moviesQuery.refetch()}>
                Retry
              </Button>
            }
          />
        ) : moviesQuery.isLoading ? (
          <MediaGridSkeleton />
        ) : (
          <>
            {totalMovies === 0 ? (
              !searchQuery && filters.length === 0 ? (
                <DataListEmpty
                  icon={<Film />}
                  title="No movies yet"
                  description="Add a movie to start building your library."
                />
              ) : (
                <DataListEmpty
                  icon={<Film />}
                  title="No matching movies"
                  description="No movies match your search or filters."
                />
              )
            ) : (
              <VirtualMediaGrid
                items={movies}
                getKey={(movie) => movie.id ?? ""}
                renderItem={(movie) => (
                  <div
                    className="flex flex-col"
                    onMouseEnter={() => prefetchMovie(movie)}
                    onFocus={() => prefetchMovie(movie)}
                  >
                    <Link href={`/dashboard/movies/${movie.id}`} className="group">
                      <div className="relative aspect-[2/3] overflow-hidden rounded-lg">
                        <MediaPicture media={movie} />
                        <DownloadedBadge complete={Boolean(movie.downloaded)} />
                      </div>
                    </Link>
                    <div className="flex flex-col gap-1.5 py-2">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        {movie.year != null && <span>{movie.year}</span>}
                        {movie.vote_average != null && (
                          <span className="ml-auto flex items-center font-medium text-yellow-600">
                            {STAR_ICON}
                            {Math.round(movie.vote_average)}/10
                          </span>
                        )}
                      </div>
                      <Link href={`/dashboard/movies/${movie.id}`} className="group">
                        <p className="line-clamp-2 min-h-[2.5rem] text-sm leading-tight font-medium group-hover:underline">
                          {movie.name}
                        </p>
                      </Link>
                      <Button
                        render={<Link href={`/dashboard/movies/${movie.id}`} />}
                        variant="outline"
                        className="w-full border-border bg-clip-border font-semibold"
                      >
                        View
                      </Button>
                    </div>
                  </div>
                )}
              />
            )}

            <MediaPagination
              page={currentPage}
              totalPages={totalPages}
              onPageChange={setCurrentPage}
              total={totalMovies}
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

"use client";

import { useQueries } from "@tanstack/react-query";
import Link from "next/link";
import dynamic from "next/dynamic";
import { AlertOctagon, AlertTriangle, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ContinueWatchingRow } from "@/components/continue-watching-row";
import { DashboardHeader } from "@/components/dashboard-header";
import { StatCards } from "@/components/stats/stat-cards";
import { MediaGridSkeleton } from "@/components/media-grid-skeleton";
import { useUser } from "@/components/providers/user-provider";
import apiClient from "@/lib/api/client";
import { dashboardImportWarningCounts, dashboardSummaryViewState } from "@/lib/dashboard-summary";

const RecommendedMediaCarousel = dynamic(
  () => import("@/components/recommended-media-carousel").then((m) => m.RecommendedMediaCarousel),
  {
    ssr: false,
    loading: () => (
      <>
        <div className="flex items-center">
          <div className="h-8 w-48 rounded bg-muted/40" />
        </div>
        <MediaGridSkeleton count={5} />
      </>
    ),
  },
);

export function DashboardHome() {
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;

  const [summaryQuery, recommendedShows, recommendedMovies] = useQueries({
    queries: [
      {
        queryKey: ["dashboard", "summary"],
        queryFn: async ({ signal }) => {
          const { data, error } = await apiClient.GET("/api/v1/dashboard/summary", { signal });
          if (error) throw error;
          return data;
        },
        staleTime: 30 * 1000,
      },
      {
        queryKey: ["shows", "recommended"],
        queryFn: async ({ signal }) => {
          const { data, error } = await apiClient.GET("/api/v1/shows/recommended", { signal });
          if (error) throw error;
          return data ?? [];
        },
        retry: 0,
      },
      {
        queryKey: ["movies", "recommended"],
        queryFn: async ({ signal }) => {
          const { data, error } = await apiClient.GET("/api/v1/movies/recommended", { signal });
          if (error) throw error;
          return data ?? [];
        },
        retry: 0,
      },
    ],
  });

  const summaryView = dashboardSummaryViewState({
    isPending: summaryQuery.isPending,
    isError: summaryQuery.isError,
    data: summaryQuery.data
      ? {
          shows: summaryQuery.data.shows,
          movies: summaryQuery.data.movies,
          torrents: summaryQuery.data.torrents,
          requestsPending: summaryQuery.data.requests_pending,
          importsFailed: summaryQuery.data.imports_failed,
          importsAmbiguous: summaryQuery.data.imports_ambiguous,
        }
      : null,
  });

  const importCounts = dashboardImportWarningCounts({
    view: summaryView,
    isSuperuser,
  });

  return (
    <>
      <DashboardHeader crumbs={[{ label: "Dashboard" }]} />
      <main className="flex flex-1 flex-col gap-10 p-4 pt-0">
        {importCounts && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm">
            <div className="flex items-center gap-2">
              {importCounts.failed > 0 && (
                <>
                  <AlertOctagon className="h-4 w-4 text-destructive" />
                  <span>
                    <strong>{importCounts.failed}</strong> failed import
                    {importCounts.failed === 1 ? "" : "s"}
                  </span>
                </>
              )}
              {importCounts.failed > 0 && importCounts.ambiguous > 0 && (
                <span className="text-muted-foreground">·</span>
              )}
              {importCounts.ambiguous > 0 && (
                <>
                  <AlertTriangle className="h-4 w-4 text-yellow-500" />
                  <span>
                    <strong>{importCounts.ambiguous}</strong> ambiguous
                  </span>
                </>
              )}
            </div>
            <Button render={<Link href="/dashboard/imports" />} size="sm" variant="outline">
              Review
            </Button>
          </div>
        )}

        {summaryView.status === "error" ? (
          <div
            role="alert"
            className="flex flex-col items-start gap-3 rounded-md border border-dashed px-4 py-6 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex items-start gap-3">
              <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">{summaryView.message}</p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void summaryQuery.refetch();
              }}
            >
              Retry
            </Button>
          </div>
        ) : summaryView.status === "pending" ? (
          <StatCards isLoading />
        ) : (
          <StatCards
            showCount={summaryView.counts.shows}
            moviesCount={summaryView.counts.movies}
            torrentCount={summaryView.counts.torrents}
            requestCount={summaryView.counts.requestsPending}
          />
        )}

        <ContinueWatchingRow />

        <div className="space-y-4">
          <RecommendedMediaCarousel
            title="Trending Shows"
            isLoading={recommendedShows.isLoading}
            errorMessage={
              recommendedShows.isError
                ? "Unable to load trending shows. The metadata provider may be unavailable."
                : null
            }
            isShow
            media={recommendedShows.data ?? []}
          />
        </div>
        <div className="space-y-4">
          <RecommendedMediaCarousel
            title="Trending Movies"
            isLoading={recommendedMovies.isLoading}
            errorMessage={
              recommendedMovies.isError
                ? "Unable to load trending movies. The metadata provider may be unavailable."
                : null
            }
            isShow={false}
            media={recommendedMovies.data ?? []}
          />
        </div>
      </main>
    </>
  );
}

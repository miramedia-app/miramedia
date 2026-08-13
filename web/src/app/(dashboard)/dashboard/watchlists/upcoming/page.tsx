"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, TriangleAlert } from "lucide-react";
import { DataListEmpty } from "@/components/data-list";
import { MediaPicture } from "@/components/media-picture";
import { Button } from "@/components/ui/button";
import {
  UpcomingControls,
  resolveUpcomingWindow,
  type UpcomingWindow,
} from "@/components/watchlists/upcoming-controls";
import { WatchlistsPageShell } from "@/components/watchlists/watchlists-nav";
import { UPCOMING_LABEL } from "@/components/watchlists/watchlists-routes";
import { useFeatures, useFeaturesStatus } from "@/components/providers/features-provider";
import apiClient from "@/lib/api/client";
import type { ActiveFilter } from "@/components/data-list/types";
import {
  filterUpcomingItems,
  formatUpcomingDateHeading,
  groupUpcomingByDate,
  posterMediaForUpcoming,
  upcomingItemCopy,
  upcomingItemHref,
  type UpcomingSort,
} from "@/lib/upcoming";

export default function UpcomingPage() {
  const {
    upcoming: upcomingEnabled,
    upcoming_default_past_days: defaultPastDays,
    upcoming_default_future_days: defaultFutureDays,
  } = useFeatures();
  const { isPending: featuresPending, isError: featuresError } = useFeaturesStatus();
  const [windowOverride, setWindowOverride] = React.useState<UpcomingWindow | null>(null);
  const window = resolveUpcomingWindow({
    override: windowOverride,
    featuresReady: !featuresPending,
    pastDays: defaultPastDays,
    futureDays: defaultFutureDays,
  });
  const [sort, setSort] = React.useState<UpcomingSort>("date-asc");
  const [search, setSearch] = React.useState("");
  const [filters, setFilters] = React.useState<ActiveFilter[]>([]);

  const upcomingQuery = useQuery({
    queryKey: ["watchlists", "upcoming", window?.start, window?.end],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/watchlists/upcoming", {
        params: { query: { start: window!.start, end: window!.end } },
        signal,
      });
      if (error) throw error;
      return data;
    },
    staleTime: 60 * 1000,
    enabled: upcomingEnabled && window != null,
  });

  const items = upcomingQuery.data?.items ?? [];
  const visibleItems = filterUpcomingItems(items, search, filters);
  const groups = groupUpcomingByDate(visibleItems, sort);
  const filtersActive = search.trim().length > 0 || filters.length > 0;

  if (!upcomingEnabled) {
    return (
      <WatchlistsPageShell crumbs={[{ label: UPCOMING_LABEL }]} mainClassName="gap-8">
        <DataListEmpty
          icon={<CalendarDays />}
          title={featuresError ? "Features could not be loaded" : "Upcoming is disabled"}
          description={
            featuresError
              ? "The feature settings request failed. Check that the backend is reachable."
              : "Enable it in System → Settings → Watchlists."
          }
        />
      </WatchlistsPageShell>
    );
  }

  return (
    <WatchlistsPageShell crumbs={[{ label: UPCOMING_LABEL }]} mainClassName="gap-8">
      {window ? (
        <UpcomingControls
          window={window}
          onWindowChange={setWindowOverride}
          sort={sort}
          onSortChange={setSort}
          search={search}
          onSearchChange={setSearch}
          filters={filters}
          onFiltersChange={setFilters}
        />
      ) : null}

      {upcomingQuery.isError ? (
        <div role="alert">
          <DataListEmpty
            icon={<TriangleAlert />}
            title="Upcoming could not be loaded"
            description="The upcoming library request failed. Check that the backend is reachable."
            action={
              <Button variant="outline" size="sm" onClick={() => void upcomingQuery.refetch()}>
                Retry
              </Button>
            }
          />
        </div>
      ) : upcomingQuery.isPending ? (
        <div className="space-y-6" aria-busy="true">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="space-y-3">
              <div className="h-4 w-40 rounded bg-muted/40" />
              <div className="h-16 rounded bg-muted/30" />
              <div className="h-16 rounded bg-muted/30" />
            </div>
          ))}
        </div>
      ) : visibleItems.length === 0 ? (
        <DataListEmpty
          icon={<CalendarDays />}
          title={filtersActive ? "No matches" : "Nothing upcoming yet"}
          description={
            filtersActive
              ? "Try clearing or adjusting your search and filters."
              : "Pending releases will show up here."
          }
        />
      ) : (
        <div className="space-y-8">
          {groups.map((group) => (
            <section key={group.date} className="space-y-3">
              <h2 className="text-sm font-medium text-muted-foreground">
                {formatUpcomingDateHeading(group.date)}
              </h2>
              <ul className="divide-y border-y">
                {group.items.map((item) => {
                  const href = upcomingItemHref(item);
                  const poster = posterMediaForUpcoming(item);
                  const copy = upcomingItemCopy(item);
                  const row = (
                    <div className="flex min-h-11 items-center gap-4 py-3">
                      <div className="h-14 w-[37px] shrink-0 overflow-hidden rounded-sm">
                        <MediaPicture media={poster} />
                      </div>
                      <div className="min-w-0 flex-1 space-y-1">
                        <p className="truncate text-sm font-medium group-hover:underline">
                          {copy.title}
                        </p>
                        {copy.subtitle ? (
                          <p className="truncate text-xs text-muted-foreground">{copy.subtitle}</p>
                        ) : null}
                      </div>
                    </div>
                  );
                  return (
                    <li key={`${item.media_type}-${item.id}`}>
                      {href ? (
                        <Link href={href} className="group block focus-visible:outline-none">
                          {row}
                        </Link>
                      ) : (
                        row
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
          {upcomingQuery.data?.truncated ? (
            <p className="text-sm text-muted-foreground">
              Not all upcoming items shown for this window.
            </p>
          ) : null}
        </div>
      )}
    </WatchlistsPageShell>
  );
}

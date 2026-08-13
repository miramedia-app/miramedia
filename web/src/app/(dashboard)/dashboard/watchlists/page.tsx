"use client";

import Link from "next/link";
import { CalendarDays, ListChecks, ListTodo, Plus } from "lucide-react";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  DataListEmpty,
  DataListSearchFilter,
  DataListToolbar,
  type ActiveFilter,
} from "@/components/data-list";
import { MediaPicture } from "@/components/media-picture";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  normalizeWatchlistName,
  validateWatchlistName,
} from "@/components/watchlists/add-to-watchlist";
import { WatchlistsPageShell } from "@/components/watchlists/watchlists-nav";
import { resolveUpcomingWindow } from "@/components/watchlists/upcoming-controls";
import {
  UPCOMING_BASE,
  WATCH_NEXT_PATH,
  watchlistDetailPath,
} from "@/components/watchlists/watchlists-routes";
import {
  buildHubCards,
  filterAndSortHubCards,
  getMyListsViewState,
  HUB_DEFAULT_SORT,
  hubFacets,
  hubSortOptions,
  type HubCard,
} from "@/components/watchlists/watchlists-hub";
import { useFeatures, useFeaturesStatus } from "@/components/providers/features-provider";
import {
  EMPTY_WATCHLISTS,
  useCreateWatchlist,
  useWatchNext,
  useWatchlists,
  WATCH_NEXT_PAGE_SIZE,
} from "@/hooks/use-watchlists";
import apiClient from "@/lib/api/client";
import { formatCappedItemCount } from "@/lib/watchlists";

export default function WatchlistsHubPage() {
  const router = useRouter();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [newListName, setNewListName] = React.useState("");
  const {
    watch_next: watchNextEnabled,
    watch_next_include_specials: watchNextIncludeSpecials,
    upcoming: upcomingEnabled,
    custom_lists: customListsEnabled,
    upcoming_default_past_days: upcomingPastDays,
    upcoming_default_future_days: upcomingFutureDays,
  } = useFeatures();
  const listsQuery = useWatchlists(customListsEnabled);
  const watchNextQuery = useWatchNext(watchNextEnabled, watchNextIncludeSpecials);
  const createWatchlist = useCreateWatchlist();

  const [search, setSearch] = React.useState("");
  const [filters, setFilters] = React.useState<ActiveFilter[]>([]);
  const [sort, setSort] = React.useState(HUB_DEFAULT_SORT);
  const { isPending: featuresPending } = useFeaturesStatus();
  const upcomingWindow = resolveUpcomingWindow({
    override: null,
    featuresReady: !featuresPending,
    pastDays: upcomingPastDays,
    futureDays: upcomingFutureDays,
  });

  const upcomingQuery = useQuery({
    queryKey: ["watchlists", "upcoming", "hub", upcomingWindow?.start, upcomingWindow?.end],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/watchlists/upcoming", {
        params: { query: { start: upcomingWindow!.start, end: upcomingWindow!.end } },
        signal,
      });
      if (error) throw error;
      return data;
    },
    staleTime: 60_000,
    enabled: upcomingEnabled && upcomingWindow != null,
  });

  const lists = customListsEnabled ? (listsQuery.data ?? EMPTY_WATCHLISTS) : EMPTY_WATCHLISTS;
  const watchNextCount = watchNextQuery.data?.length ?? 0;
  const watchNextCover = watchNextQuery.data?.[0]?.poster_media_id ?? null;
  const watchNextTruncated = (watchNextQuery.data?.length ?? 0) >= WATCH_NEXT_PAGE_SIZE;
  const upcomingItems = upcomingQuery.data?.items ?? [];
  const upcomingCount = upcomingItems.length;
  const upcomingCover = upcomingItems[0]?.poster_id ?? null;
  const upcomingTruncated = upcomingQuery.data?.truncated ?? false;

  const hubCards = React.useMemo(
    () =>
      buildHubCards({
        lists,
        watchNextCount,
        watchNextCover,
        upcomingCount,
        upcomingCover,
        includeWatchNext: watchNextEnabled,
        includeUpcoming: upcomingEnabled,
        watchNextTruncated,
        upcomingTruncated,
      }),
    [
      lists,
      watchNextCount,
      watchNextCover,
      upcomingCount,
      upcomingCover,
      watchNextEnabled,
      upcomingEnabled,
      watchNextTruncated,
      upcomingTruncated,
    ],
  );

  const visibleCards = React.useMemo(
    () => filterAndSortHubCards(hubCards, search, filters, sort),
    [hubCards, search, filters, sort],
  );

  const listsViewState = customListsEnabled
    ? getMyListsViewState({
        isPending: listsQuery.isPending,
        isError: listsQuery.isError,
        count: lists.length,
      })
    : "ready";

  const queryActive = search.trim().length > 0 || filters.length > 0;
  const gridEmpty =
    listsViewState !== "pending" && listsViewState !== "error" && visibleCards.length === 0;

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    const validation = validateWatchlistName(newListName);
    if (validation) {
      toast.error(validation);
      return;
    }
    try {
      const created = await createWatchlist.mutateAsync({
        name: normalizeWatchlistName(newListName),
      });
      setCreateOpen(false);
      setNewListName("");
      router.push(watchlistDetailPath(created.id));
    } catch {
      // Toasts are handled in mutation hooks.
    }
  }

  const createDialog = (
    <Dialog open={createOpen} onOpenChange={setCreateOpen}>
      <DialogTrigger
        render={
          <Button size="default" className="text-xs">
            <Plus className="size-4" />
            Create list
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleCreate}>
          <DialogHeader>
            <DialogTitle>Create watchlist</DialogTitle>
          </DialogHeader>
          <div className="grid gap-2 py-4">
            <Label htmlFor="hub-new-list-name">Name</Label>
            <Input
              id="hub-new-list-name"
              value={newListName}
              onChange={(event) => setNewListName(event.target.value)}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={createWatchlist.isPending}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );

  return (
    <WatchlistsPageShell mainClassName="gap-6">
      <DataListToolbar
        searchFilter={
          <DataListSearchFilter
            search={search}
            onSearchChange={setSearch}
            facets={hubFacets}
            filters={filters}
            onFiltersChange={setFilters}
            placeholder="Search or filter lists…"
          />
        }
        sortOptions={hubSortOptions}
        sort={sort}
        onSortChange={setSort}
        trailing={customListsEnabled ? createDialog : null}
      />

      {listsViewState === "error" ? (
        <p className="text-sm text-pretty text-muted-foreground" role="alert">
          Lists could not be loaded.
        </p>
      ) : listsViewState === "pending" ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" aria-busy="true">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-24 rounded-lg bg-muted/30" />
          ))}
        </div>
      ) : gridEmpty ? (
        <DataListEmpty
          icon={<ListChecks />}
          title={queryActive ? "No matches" : "No custom lists yet"}
          description={
            queryActive
              ? "Try a different search or clear filters."
              : "Create a list to track movies, shows, and episodes."
          }
          action={queryActive ? undefined : createDialog}
        />
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {visibleCards.map((card) => (
            <li key={card.id}>
              <HubListCard
                card={card}
                itemCountPending={
                  (card.kind === "watch-next" && watchNextQuery.isPending) ||
                  (card.kind === "upcoming" && upcomingQuery.isPending)
                }
              />
            </li>
          ))}
        </ul>
      )}
    </WatchlistsPageShell>
  );
}

function hubCardHref(card: HubCard): string {
  if (card.kind === "watch-next") return WATCH_NEXT_PATH;
  if (card.kind === "upcoming") return UPCOMING_BASE;
  return watchlistDetailPath(card.id);
}

function HubListCard({ card, itemCountPending }: { card: HubCard; itemCountPending: boolean }) {
  const fallbackIcon =
    card.kind === "watch-next" ? (
      <ListTodo className="size-5 text-muted-foreground" />
    ) : card.kind === "upcoming" ? (
      <CalendarDays className="size-5 text-muted-foreground" />
    ) : (
      <ListChecks className="size-5 text-muted-foreground" />
    );

  const itemCountLabel = itemCountPending
    ? "…"
    : formatCappedItemCount(card.itemCount, card.truncated ?? false);

  return (
    <Link
      href={hubCardHref(card)}
      className="group flex h-full items-center gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <div className="h-20 w-[3.35rem] shrink-0 overflow-hidden rounded-md">
        {card.coverId ? (
          <MediaPicture media={{ id: card.coverId, name: card.name, year: null }} />
        ) : (
          <div className="flex size-full items-center justify-center rounded-md bg-muted">
            {fallbackIcon}
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="truncate text-sm font-medium group-hover:underline">{card.name}</p>
        <p className="line-clamp-2 text-xs text-pretty text-muted-foreground">{card.description}</p>
        <p className="text-xs text-muted-foreground tabular-nums">{itemCountLabel}</p>
      </div>
    </Link>
  );
}

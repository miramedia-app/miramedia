import type { ActiveFilter, FacetDef, SortOption } from "@/components/data-list/types";
import {
  UPCOMING_LABEL,
  UPCOMING_OVERVIEW,
  WATCH_NEXT_LABEL,
} from "@/components/watchlists/watchlists-routes";
import type { WatchlistSummary } from "@/lib/watchlists";
import { asyncListViewState } from "@/lib/watchlists";

export type HubCardKind = "watch-next" | "upcoming" | "list";

export type HubCard = {
  id: string;
  kind: HubCardKind;
  name: string;
  description: string;
  itemCount: number;
  coverId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  truncated?: boolean;
};

const WATCH_NEXT_DESCRIPTION = "Next downloaded episode for each tracked show.";

export function watchNextHubCard(opts: {
  itemCount: number;
  coverId: string | null;
  truncated?: boolean;
}): HubCard {
  return {
    id: "watch-next",
    kind: "watch-next",
    name: WATCH_NEXT_LABEL,
    description: WATCH_NEXT_DESCRIPTION,
    itemCount: opts.itemCount,
    coverId: opts.coverId,
    createdAt: null,
    updatedAt: null,
    truncated: opts.truncated,
  };
}

export function upcomingHubCard(opts: {
  itemCount: number;
  coverId: string | null;
  truncated?: boolean;
}): HubCard {
  return {
    id: "upcoming",
    kind: "upcoming",
    name: UPCOMING_LABEL,
    description: UPCOMING_OVERVIEW,
    itemCount: opts.itemCount,
    coverId: opts.coverId,
    createdAt: null,
    updatedAt: null,
    truncated: opts.truncated,
  };
}

export function listToHubCard(list: WatchlistSummary): HubCard {
  return {
    id: list.id,
    kind: "list",
    name: list.name,
    description: list.description?.trim() ? list.description : "No description",
    itemCount: list.item_count,
    coverId: list.cover_poster_media_id ?? null,
    createdAt: list.created_at ?? null,
    updatedAt: list.updated_at ?? null,
  };
}

export function buildHubCards(opts: {
  lists: WatchlistSummary[];
  watchNextCount: number;
  watchNextCover: string | null;
  upcomingCount: number;
  upcomingCover: string | null;
  includeWatchNext?: boolean;
  includeUpcoming?: boolean;
  watchNextTruncated?: boolean;
  upcomingTruncated?: boolean;
}): HubCard[] {
  const cards: HubCard[] = [];
  if (opts.includeWatchNext ?? true) {
    cards.push(
      watchNextHubCard({
        itemCount: opts.watchNextCount,
        coverId: opts.watchNextCover,
        truncated: opts.watchNextTruncated,
      }),
    );
  }
  if (opts.includeUpcoming ?? true) {
    cards.push(
      upcomingHubCard({
        itemCount: opts.upcomingCount,
        coverId: opts.upcomingCover,
        truncated: opts.upcomingTruncated,
      }),
    );
  }
  cards.push(...opts.lists.map(listToHubCard));
  return cards;
}

export function hubCardSearchMatch(card: HubCard, search: string): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  return card.name.toLowerCase().includes(q) || card.description.toLowerCase().includes(q);
}

function facetHit(values: string[], value: string, operator: ActiveFilter["operator"]): boolean {
  const hit = values.includes(value);
  return operator === "excludes" || operator === "is_not" ? !hit : hit;
}

export const hubFacets: FacetDef<HubCard>[] = [
  {
    id: "type",
    label: "Type",
    options: [
      { value: "built-in", label: "Built-in" },
      { value: "custom", label: "Custom" },
    ],
    predicate: (card, values, operator) => {
      const type = card.kind === "list" ? "custom" : "built-in";
      return facetHit(values, type, operator);
    },
  },
  {
    id: "items",
    label: "Items",
    options: [
      { value: "empty", label: "Empty" },
      { value: "has-items", label: "Has items" },
    ],
    predicate: (card, values, operator) => {
      const bucket = card.itemCount > 0 ? "has-items" : "empty";
      return facetHit(values, bucket, operator);
    },
  },
];

function timestamp(value: string | null): number {
  if (!value) return 0;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
}

export const hubSortOptions: SortOption<HubCard>[] = [
  { id: "name-asc", label: "Name A–Z", compare: (a, b) => a.name.localeCompare(b.name) },
  { id: "name-desc", label: "Name Z–A", compare: (a, b) => b.name.localeCompare(a.name) },
  {
    id: "newest",
    label: "Newest first",
    compare: (a, b) => timestamp(b.createdAt) - timestamp(a.createdAt),
  },
  {
    id: "oldest",
    label: "Oldest first",
    compare: (a, b) => timestamp(a.createdAt) - timestamp(b.createdAt),
  },
  {
    id: "updated",
    label: "Recently updated",
    compare: (a, b) => timestamp(b.updatedAt) - timestamp(a.updatedAt),
  },
  {
    id: "items-desc",
    label: "Most items",
    compare: (a, b) => b.itemCount - a.itemCount || a.name.localeCompare(b.name),
  },
  {
    id: "items-asc",
    label: "Fewest items",
    compare: (a, b) => a.itemCount - b.itemCount || a.name.localeCompare(b.name),
  },
];

export const HUB_DEFAULT_SORT = "name-asc";

const PINNED_ORDER: Record<Exclude<HubCardKind, "list">, number> = {
  "watch-next": 0,
  upcoming: 1,
};

/** Filter + sort hub cards. Built-ins stay pinned above custom lists. */
export function filterAndSortHubCards(
  cards: HubCard[],
  search: string,
  filters: ActiveFilter[],
  sortId: string = HUB_DEFAULT_SORT,
): HubCard[] {
  const byId = new Map(hubFacets.map((f) => [f.id, f]));
  const filtered = cards.filter((card) => {
    if (!hubCardSearchMatch(card, search)) return false;
    return filters.every((f) => {
      const facet = byId.get(f.facetId);
      return facet ? facet.predicate(card, f.values, f.operator) : true;
    });
  });

  const pinned = filtered
    .filter((card): card is HubCard & { kind: "watch-next" | "upcoming" } => card.kind !== "list")
    .sort((a, b) => PINNED_ORDER[a.kind] - PINNED_ORDER[b.kind]);

  const lists = filtered.filter((card) => card.kind === "list");
  const compare =
    hubSortOptions.find((option) => option.id === sortId)?.compare ?? hubSortOptions[0]!.compare;
  lists.sort(compare);

  return [...pinned, ...lists];
}

/** @deprecated Prefer filterAndSortHubCards; kept for older call sites/tests. */
export function filterWatchlistSummaries(
  lists: WatchlistSummary[],
  search: string,
): WatchlistSummary[] {
  const q = search.trim().toLowerCase();
  if (!q) return lists;
  return lists.filter(
    (list) =>
      list.name.toLowerCase().includes(q) || (list.description ?? "").toLowerCase().includes(q),
  );
}

export function pinnedCardMatchesSearch(label: string, search: string): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  return label.toLowerCase().includes(q);
}

export function watchNextCardMatchesSearch(search: string): boolean {
  return pinnedCardMatchesSearch(WATCH_NEXT_LABEL, search);
}

export function upcomingCardMatchesSearch(search: string): boolean {
  return pinnedCardMatchesSearch(UPCOMING_LABEL, search);
}

export function getMyListsViewState(opts: { isPending: boolean; isError: boolean; count: number }) {
  return asyncListViewState({
    isPending: opts.isPending,
    isError: opts.isError,
    isEmpty: opts.count === 0,
  });
}

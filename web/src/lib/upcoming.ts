import type { components } from "@/lib/api/api";
import type { ActiveFilter, FacetDef } from "@/components/data-list/types";
import { formatListItemCopy, type ListItemCopy } from "@/lib/watchlists";

export type UpcomingItem = components["schemas"]["UpcomingItem"];

const dateHeadingFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
});

export function upcomingItemCopy(item: UpcomingItem): ListItemCopy {
  const copy =
    item.media_type === "movie"
      ? formatListItemCopy({ title: item.title, mediaKind: "movie" })
      : formatListItemCopy({
          title: item.title,
          mediaKind: "episode",
          showName: item.show_name,
          seasonNumber: item.season_number,
          episodeNumber: item.episode_number,
        });
  const when = formatAirTime(item.air_time);
  if (!when) return copy;
  return {
    title: copy.title,
    subtitle: copy.subtitle ? `${copy.subtitle} · ${when}` : when,
  };
}

export function upcomingItemHref(item: UpcomingItem): string | null {
  if (item.media_type === "movie") {
    return item.id ? `/dashboard/movies/${item.id}` : null;
  }
  return item.show_id ? `/dashboard/shows/${item.show_id}` : null;
}

export function formatUpcomingDateHeading(isoDate: string): string {
  // API dates are YYYY-MM-DD calendar dates — parse as local noon to avoid UTC shift.
  const parsed = parseIsoDate(isoDate);
  return parsed ? dateHeadingFormatter.format(parsed) : isoDate;
}

const airTimeFormatter = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
});

/** Format an air time-of-day ("HH:MM[:SS]") for display, e.g. "9:00 PM". */
export function formatAirTime(value?: string | null): string | null {
  if (!value) return null;
  const [h, m] = value.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return airTimeFormatter.format(d);
}

export type UpcomingSort = "date-asc" | "date-desc";

/** YYYY-MM-DD in the *local* calendar (toISOString would shift across UTC). */
export function toIsoDate(value: Date): string {
  const month = `${value.getMonth() + 1}`.padStart(2, "0");
  const day = `${value.getDate()}`.padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

/** Inverse of toIsoDate: local noon, so DST never rolls the day over. */
export function parseIsoDate(isoDate: string): Date | null {
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d, 12);
}

export function groupUpcomingByDate(
  items: UpcomingItem[],
  sort: UpcomingSort = "date-asc",
): { date: string; items: UpcomingItem[] }[] {
  const groups = new Map<string, UpcomingItem[]>();
  for (const item of items) {
    const key = item.date;
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  const direction = sort === "date-desc" ? -1 : 1;
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b) * direction)
    .map(([date, groupItems]) => ({ date, items: groupItems }));
}

/** Free-text search over an upcoming row's title and show name. */
export function upcomingSearchMatch(item: UpcomingItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return item.title.toLowerCase().includes(q) || (item.show_name ?? "").toLowerCase().includes(q);
}

/** Facets for the upcoming search/filter combobox. */
export const upcomingFacets: FacetDef<UpcomingItem>[] = [
  {
    id: "type",
    label: "Type",
    options: [
      { value: "episode", label: "Episode" },
      { value: "movie", label: "Movie" },
    ],
    predicate: (item, values, operator) => {
      const hit = values.includes(item.media_type);
      return operator === "excludes" || operator === "is_not" ? !hit : hit;
    },
  },
  {
    id: "status",
    label: "Status",
    options: [
      { value: "downloaded", label: "Downloaded" },
      { value: "pending", label: "Pending" },
    ],
    predicate: (item, values, operator) => {
      const state = item.downloaded ? "downloaded" : "pending";
      const hit = values.includes(state);
      return operator === "excludes" || operator === "is_not" ? !hit : hit;
    },
  },
];

/** Apply free-text search + active facet filters to upcoming rows. */
export function filterUpcomingItems(
  items: UpcomingItem[],
  search: string,
  filters: ActiveFilter[],
): UpcomingItem[] {
  const byId = new Map(upcomingFacets.map((f) => [f.id, f]));
  return items.filter((item) => {
    if (!upcomingSearchMatch(item, search)) return false;
    return filters.every((f) => {
      const facet = byId.get(f.facetId);
      return facet ? facet.predicate(item, f.values, f.operator) : true;
    });
  });
}

export function posterMediaForUpcoming(item: UpcomingItem): {
  id: string | null;
  name: string;
  year: number | null;
} {
  return {
    id: item.poster_id ?? null,
    name: item.show_name ?? item.title,
    year: null,
  };
}

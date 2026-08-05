export type Site = {
  id: string;
  name: string;
  url: string;
  available_urls?: string[];
  api_key?: string | null;
  supports_tv: boolean;
  supports_movies: boolean;
  cloudflare_protected?: boolean;
  site_type: string;
  enabled: boolean;
  is_preloaded?: boolean;
  priority?: number | null;
  last_test_status?: string | null;
  last_test_at?: string | null;
  last_success_at?: string | null;
};

export const siteTypeLabel: Record<string, string> = {
  native: "System",
  torznab: "Custom",
};

/** Free-text search predicate for an indexer site row. */
export function indexerSearchMatch(s: Site, q: string): boolean {
  return s.name.toLowerCase().includes(q) || (s.url ?? "").toLowerCase().includes(q);
}

/** Facet bucket for a site's last test outcome. */
export function siteTestFacetValue(s: Site): "error" | "ok" {
  return s.last_test_status === "error" ? "error" : "ok";
}

export interface HealthGroup {
  key: string;
  label: string;
  sortOrder: number;
}

/** Health grouping bucket: failed → healthy → untested. */
export function siteHealthGroup(s: Site): HealthGroup {
  if (s.last_test_status === "error") return { key: "failed", label: "Failed", sortOrder: 0 };
  if (s.last_success_at) return { key: "healthy", label: "Healthy", sortOrder: 1 };
  return { key: "untested", label: "Untested", sortOrder: 2 };
}

/** Effective priority (default 100 when unset). Lower is searched first. */
export function sitePriority(s: Site): number {
  return s.priority ?? 100;
}

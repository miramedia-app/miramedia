"use client";

import * as React from "react";
import { ArrowUpDownIcon, Plus, Search as SearchIcon, X as XIcon } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DataListSearchFilter } from "@/components/data-list";
import type { ActiveFilter, FacetDef } from "@/components/data-list";

/** Apply active facet filters (AND across facets) to a list. */
export function applyFacetFilters<T>(
  items: T[],
  facets: FacetDef<T>[],
  filters: ActiveFilter[],
): T[] {
  if (filters.length === 0) return items;
  // Build id → facet map once so per-item evaluation is O(filters), not
  // O(filters × facets).
  const facetById = new Map<string, FacetDef<T>>();
  for (const f of facets) facetById.set(f.id, f);
  return items.filter((item) =>
    filters.every((f) => {
      const facet = facetById.get(f.facetId);
      if (!facet || f.values.length === 0) return true;
      return facet.predicate(item, f.values, f.operator);
    }),
  );
}

export const sortOptions = [
  { value: "name-asc", label: "Name A–Z" },
  { value: "name-desc", label: "Name Z–A" },
  { value: "year-desc", label: "Newest first" },
  { value: "year-asc", label: "Oldest first" },
  { value: "rating-desc", label: "Highest rated" },
  { value: "rating-asc", label: "Lowest rated" },
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function MediaGridControls<T = any>({
  searchQuery,
  onSearchChange,
  sortBy,
  onSortChange,
  searchPlaceholder,
  addHref,
  addLabel,
  facets,
  filters,
  onFiltersChange,
}: {
  searchQuery: string;
  onSearchChange: (v: string) => void;
  sortBy: string;
  onSortChange: (v: string) => void;
  searchPlaceholder: string;
  addHref: string;
  addLabel: string;
  facets?: FacetDef<T>[];
  filters?: ActiveFilter[];
  onFiltersChange?: (next: ActiveFilter[]) => void;
}) {
  const sortLabel = sortOptions.find((o) => o.value === sortBy)?.label ?? "Sort";
  const useFacets = facets != null && filters != null && onFiltersChange != null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {useFacets ? (
        <DataListSearchFilter<T>
          search={searchQuery}
          onSearchChange={onSearchChange}
          facets={facets}
          filters={filters}
          onFiltersChange={onFiltersChange}
          placeholder={searchPlaceholder}
          className="min-w-[260px]"
        />
      ) : (
        <div className="flex h-8 min-w-[260px] flex-1 items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-sm shadow-xs transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50">
          <SearchIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            type="search"
            placeholder={searchPlaceholder}
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="min-w-[80px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {searchQuery && (
            <button
              type="button"
              className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => onSearchChange("")}
              aria-label="Clear"
            >
              <XIcon className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="outline" size="default" className="gap-1 text-xs">
              <ArrowUpDownIcon className="h-4 w-4" />
              {sortLabel}
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Sort by</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={sortBy} onValueChange={onSortChange}>
              {sortOptions.map((o) => (
                <DropdownMenuRadioItem key={o.value} value={o.value}>
                  {o.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>

      <span className="hidden h-6 w-px bg-border sm:block" />

      <Button size="default" className="gap-1 text-xs" render={<Link href={addHref} />}>
        <Plus className="h-4 w-4" />
        {addLabel}
      </Button>
    </div>
  );
}

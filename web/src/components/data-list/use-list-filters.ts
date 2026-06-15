"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { ActiveFilter, FilterOperator } from "./types";

const OP_PREFIX: Record<FilterOperator, string> = {
  is: "",
  is_not: "!",
  includes: "",
  excludes: "!",
};

function parseFilterParam(raw: string | null): ActiveFilter[] {
  if (!raw) return [];
  return raw
    .split("&")
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment) => {
      const [rawKey, rawValues = ""] = segment.split(":");
      if (!rawKey) return null;
      const negated = rawKey.startsWith("!");
      const facetId = negated ? rawKey.slice(1) : rawKey;
      const values = rawValues
        .split(",")
        .map((v) => decodeURIComponent(v.trim()))
        .filter(Boolean);
      if (values.length === 0) return null;
      const operator: FilterOperator = negated ? "excludes" : "includes";
      return { facetId, operator, values } as ActiveFilter;
    })
    .filter((f): f is ActiveFilter => f != null);
}

function serializeFilters(filters: ActiveFilter[]): string {
  return filters
    .filter((f) => f.values.length > 0)
    .map(
      (f) =>
        `${OP_PREFIX[f.operator]}${f.facetId}:${f.values
          .map((v) => encodeURIComponent(v))
          .join(",")}`,
    )
    .join("&");
}

export interface UseListFiltersOptions {
  /** Sync to URL search params. Defaults to true. */
  urlSync?: boolean;
  /** Param name for filters (default 'f'). */
  filterParam?: string;
  /** Param name for free-text search (default 'q'). */
  searchParam?: string;
  /** Param name for sort (default 's'). */
  sortParam?: string;
  /** Param name for group-by (default 'g'). */
  groupParam?: string;
  /** Param name for page (default 'p'). */
  pageParam?: string;
  /** Param name for page-size (default 'ps'). */
  pageSizeParam?: string;
  /** Default search value. */
  defaultSearch?: string;
  /** Default sort id. */
  defaultSort?: string;
  /** Default group-by id. */
  defaultGroup?: string;
  /** Default page size (default 50). */
  defaultPageSize?: number;
}

export interface ListFiltersState {
  filters: ActiveFilter[];
  setFilters: (next: ActiveFilter[] | ((prev: ActiveFilter[]) => ActiveFilter[])) => void;
  search: string;
  setSearch: (next: string) => void;
  sort: string;
  setSort: (next: string) => void;
  group: string;
  setGroup: (next: string) => void;
  page: number;
  setPage: (next: number) => void;
  pageSize: number;
  setPageSize: (next: number) => void;
  clearAll: () => void;
}

export function useListFilters(opts: UseListFiltersOptions = {}): ListFiltersState {
  const {
    urlSync = true,
    filterParam = "f",
    searchParam = "q",
    sortParam = "s",
    groupParam = "g",
    pageParam = "p",
    pageSizeParam = "ps",
    defaultSearch = "",
    defaultSort = "",
    defaultGroup = "",
    defaultPageSize = 50,
  } = opts;

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const initialFromUrl = React.useMemo(() => {
    if (!urlSync) return null;
    const pageRaw = searchParams.get(pageParam);
    const psRaw = searchParams.get(pageSizeParam);
    return {
      filters: parseFilterParam(searchParams.get(filterParam)),
      search: searchParams.get(searchParam) ?? defaultSearch,
      sort: searchParams.get(sortParam) ?? defaultSort,
      group: searchParams.get(groupParam) ?? defaultGroup,
      page: pageRaw ? Math.max(1, Number.parseInt(pageRaw, 10) || 1) : 1,
      pageSize: psRaw
        ? Math.max(1, Number.parseInt(psRaw, 10) || defaultPageSize)
        : defaultPageSize,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [filters, setFiltersState] = React.useState<ActiveFilter[]>(initialFromUrl?.filters ?? []);
  const [search, setSearchState] = React.useState<string>(initialFromUrl?.search ?? defaultSearch);
  const [sort, setSortState] = React.useState<string>(initialFromUrl?.sort ?? defaultSort);
  const [group, setGroupState] = React.useState<string>(initialFromUrl?.group ?? defaultGroup);
  const [page, setPageState] = React.useState<number>(initialFromUrl?.page ?? 1);
  const [pageSize, setPageSizeState] = React.useState<number>(
    initialFromUrl?.pageSize ?? defaultPageSize,
  );

  // Reset to page 1 when filter inputs change
  const filterSignature = React.useMemo(() => serializeFilters(filters), [filters]);
  const isFirstRunRef = React.useRef(true);
  React.useEffect(() => {
    if (isFirstRunRef.current) {
      isFirstRunRef.current = false;
      return;
    }
    setPageState(1);
  }, [filterSignature, search, sort, group]);

  // Debounce URL writes so per-keystroke search doesn't push history listeners
  // each character. 200ms is below user-perceived lag and coalesces bursts.
  React.useEffect(() => {
    if (!urlSync) return;
    const handle = setTimeout(() => {
      const params = new URLSearchParams(searchParams.toString());
      const fStr = serializeFilters(filters);
      if (fStr) params.set(filterParam, fStr);
      else params.delete(filterParam);
      if (search) params.set(searchParam, search);
      else params.delete(searchParam);
      if (sort && sort !== defaultSort) params.set(sortParam, sort);
      else params.delete(sortParam);
      if (group && group !== defaultGroup) params.set(groupParam, group);
      else params.delete(groupParam);
      if (page > 1) params.set(pageParam, String(page));
      else params.delete(pageParam);
      if (pageSize !== defaultPageSize) params.set(pageSizeParam, String(pageSize));
      else params.delete(pageSizeParam);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    }, 200);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterSignature, search, sort, group, page, pageSize]);

  const setFilters = React.useCallback(
    (next: ActiveFilter[] | ((prev: ActiveFilter[]) => ActiveFilter[])) => {
      setFiltersState((prev) => (typeof next === "function" ? next(prev) : next));
    },
    [],
  );

  return {
    filters,
    setFilters,
    search,
    setSearch: setSearchState,
    sort,
    setSort: setSortState,
    group,
    setGroup: setGroupState,
    page,
    setPage: setPageState,
    pageSize,
    setPageSize: setPageSizeState,
    clearAll: React.useCallback(() => {
      setFiltersState([]);
      setSearchState(defaultSearch);
    }, [defaultSearch]),
  };
}

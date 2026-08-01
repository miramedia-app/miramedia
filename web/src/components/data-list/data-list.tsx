"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { SelectionBar } from "@/components/selection-bar";
import { DataListBulkBar } from "./data-list-bulk-bar";
import { DataListEmpty } from "./data-list-empty";
import { DataListGroupHeader, useCollapsedGroups } from "./data-list-group";
import { DataListPagination } from "./data-list-pagination";
import { DataListHeaderRow, DataListRow } from "./data-list-row";
import { DataListSearchFilter } from "./data-list-search-filter";
import { DataListSkeleton } from "./data-list-skeleton";
import { DataListToolbar } from "./data-list-toolbar";
import { isRowExpanded, nextExpandedRows } from "./expand-utils";
import { nextFocusId } from "./focus-utils";
import { countGroups } from "./grouping-utils";
import { useListFilters } from "./use-list-filters";
import { useListHotkeys } from "./use-list-hotkeys";
import { selectionHeaderState, useListSelection } from "./use-list-selection";
import type {
  ActiveFilter,
  BulkAction,
  ColumnDef,
  DataListDensity,
  FacetDef,
  GroupByDef,
  SortOption,
} from "./types";

export interface DataListProps<T> {
  data: T[];
  getId: (item: T) => string;
  columns: ColumnDef<T>[];

  /** Free-text search predicate. */
  searchMatch?: (item: T, query: string) => boolean;
  searchPlaceholder?: string;

  facets?: FacetDef<T>[];
  sortOptions?: SortOption<T>[];
  defaultSort?: string;

  groupings?: GroupByDef<T>[];
  defaultGroupId?: string;

  bulkActions?: BulkAction<T>[];
  /** Ids that are not selectable (e.g. current user). */
  unselectableIds?: Set<string>;
  /** When true, hides checkbox column and disables bulk. */
  disableSelection?: boolean;

  rowActions?: (item: T) => React.ReactNode;
  /** Fixed width for the trailing actions column. Defaults to '88px'. */
  rowActionsWidth?: string;
  /** Free-form content rendered below the row when expanded. Return null to disable expand for an item. */
  expandedContent?: (item: T) => React.ReactNode | null;
  /**
   * Cheap predicate for whether a row can expand. Defaults to true when
   * `expandedContent` is provided. Pass this whenever `expandedContent` can
   * return null for some rows, or whenever the expanded tree is nontrivial —
   * it lets collapsed rows skip building that tree entirely.
   */
  isExpandable?: (item: T) => boolean;
  /** Default expanded state for new rows. */
  defaultExpanded?: boolean;
  onRowOpen?: (item: T) => void;

  toolbarLeading?: React.ReactNode;
  toolbarTrailing?: React.ReactNode;

  density?: DataListDensity;
  loading?: boolean;
  emptyTitle?: React.ReactNode;
  emptyDescription?: React.ReactNode;
  emptyIcon?: React.ReactNode;
  emptyAction?: React.ReactNode;

  /** Sync filter/search/sort/group to URL. Defaults to true. */
  urlSync?: boolean;
  /** localStorage key for collapsed group state. */
  collapseStorageKey?: string;
  /** Default rows per page (default 50). Pass 0 or false to disable pagination. */
  pageSize?: number;
  /** Selectable page-size options for the footer dropdown. */
  pageSizeOptions?: number[];

  /** Show table-style header row above the list. Defaults true. */
  showHeader?: boolean;

  /**
   * Bulk action bar style. "inline" (default) shows a static bordered bar
   * above the list (matches show detail page). "floating" shows a centered pill.
   */
  bulkBarVariant?: "floating" | "inline";

  className?: string;
}

export function DataList<T>({
  data,
  getId,
  columns,
  searchMatch,
  searchPlaceholder,
  facets,
  sortOptions,
  defaultSort,
  groupings,
  defaultGroupId,
  bulkActions,
  unselectableIds,
  disableSelection,
  rowActions,
  rowActionsWidth = "88px",
  expandedContent,
  isExpandable,
  defaultExpanded = false,
  onRowOpen,
  toolbarLeading,
  toolbarTrailing,
  density = "standard",
  loading,
  emptyTitle = "Nothing here",
  emptyDescription,
  emptyIcon,
  emptyAction,
  urlSync = true,
  collapseStorageKey,
  pageSize: defaultPageSize = 50,
  pageSizeOptions = [20, 50, 100, 200],
  showHeader = true,
  bulkBarVariant = "inline",
  className,
}: DataListProps<T>) {
  const paginationEnabled = !!defaultPageSize;
  const filtersState = useListFilters({
    urlSync,
    defaultSort,
    defaultGroup: defaultGroupId,
    defaultPageSize: defaultPageSize || 50,
  });
  const {
    filters,
    setFilters,
    search,
    setSearch,
    sort,
    setSort,
    group,
    setGroup,
    page,
    setPage,
    pageSize,
    setPageSize,
  } = filtersState;

  const searchRef = React.useRef<HTMLInputElement>(null);

  // Defer only the search text — typing into the input is the most latency-
  // sensitive interaction. `filters` and `sort` are already URL-debounced
  // (use-list-filters.ts:160) so deferring them too would just make chip
  // changes visibly stale.
  const deferredSearch = React.useDeferredValue(search);

  // Precompute facet lookup map once per facets change so per-item filter
  // evaluation is O(1) instead of O(F) for each active filter.
  const facetById = React.useMemo(() => {
    if (!facets) return null;
    const map = new Map<string, (typeof facets)[number]>();
    for (const f of facets) map.set(f.id, f);
    return map;
  }, [facets]);

  const filtered = React.useMemo(() => {
    let out = data;
    if (deferredSearch && searchMatch) {
      const q = deferredSearch.trim().toLowerCase();
      if (q) out = out.filter((item) => searchMatch(item, q));
    }
    if (facetById && filters.length > 0) {
      out = out.filter((item) =>
        filters.every((f) => {
          const facet = facetById.get(f.facetId);
          if (!facet) return true;
          return facet.predicate(item, f.values, f.operator);
        }),
      );
    }
    return out;
  }, [data, deferredSearch, searchMatch, facetById, filters]);

  const sorted = React.useMemo(() => {
    if (!sort || !sortOptions) return filtered;
    const opt = sortOptions.find((o) => o.id === sort);
    if (!opt) return filtered;
    return [...filtered].sort(opt.compare);
  }, [filtered, sort, sortOptions]);

  const totalFiltered = sorted.length;

  const paged = React.useMemo(() => {
    if (!paginationEnabled) return sorted;
    const start = (page - 1) * pageSize;
    return sorted.slice(start, start + pageSize);
  }, [sorted, page, pageSize, paginationEnabled]);

  // Snap to last page if current page exceeds totals (after filter narrowing).
  const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize));
  React.useEffect(() => {
    if (paginationEnabled && page > totalPages) setPage(totalPages);
  }, [paginationEnabled, page, totalPages, setPage]);

  const activeGrouping = React.useMemo(() => {
    if (!groupings || !group || group === "none") return null;
    return groupings.find((g) => g.id === group) ?? null;
  }, [groupings, group]);

  const grouped = React.useMemo(() => {
    if (!activeGrouping) return null;
    const map = new Map<
      string,
      { key: string; label: React.ReactNode; sortOrder: number; items: T[] }
    >();
    for (const item of paged) {
      const g = activeGrouping.getGroup(item);
      const existing = map.get(g.key);
      if (existing) existing.items.push(item);
      else
        map.set(g.key, {
          key: g.key,
          label: g.label,
          sortOrder: g.sortOrder ?? 0,
          items: [item],
        });
    }
    return Array.from(map.values()).sort((a, b) => a.sortOrder - b.sortOrder);
  }, [paged, activeGrouping]);

  // Group sizes over the FULL filtered set. `grouped` above only sees the
  // current page (pagination runs first), so headers would otherwise report a
  // per-page count that disagrees with the toolbar's "Select all N".
  const groupTotals = React.useMemo(() => {
    if (!activeGrouping) return null;
    return countGroups(sorted, activeGrouping.getGroup);
  }, [sorted, activeGrouping]);

  // Memoize so identity is stable across renders — keeps useListSelection
  // refs intact and avoids forcing the idIndex Map to rebuild every render.
  const visibleIds = React.useMemo(() => paged.map(getId), [paged, getId]);
  // Full filtered id list (pre-pagination) — what "Select all N" acts on.
  const allSelectableIds = React.useMemo(() => sorted.map(getId), [sorted, getId]);

  const selection = useListSelection({
    ids: visibleIds,
    allIds: allSelectableIds,
    disabledIds: unselectableIds,
  });

  const { collapsed, toggle: toggleGroup } = useCollapsedGroups(collapseStorageKey);

  // Focus is tracked by id, not by index: search/sort/page/SSE churn rewrites
  // `visibleIds`, and a bare index would then point at whatever row happens to
  // land there. The index is re-derived per move (see `nextFocusId`), so a move
  // after such a change restarts from the top and `Enter`/`x` on an id that has
  // scrolled out of the visible window no-op.
  const [focusedId, setFocusedId] = React.useState<string | null>(null);
  const focusVisible = focusedId != null && visibleIds.includes(focusedId);

  useListHotkeys({
    onMoveDown: () => setFocusedId(nextFocusId(visibleIds, focusedId, 1)),
    onMoveUp: () => setFocusedId(nextFocusId(visibleIds, focusedId, -1)),
    onToggleSelect: () => {
      if (focusVisible && focusedId) selection.toggle(focusedId);
    },
    onRangeExtendDown: () => {
      const tgt = nextFocusId(visibleIds, focusedId, 1);
      const cur = focusVisible ? focusedId : null;
      if (cur && tgt) selection.selectRange(cur, tgt);
      setFocusedId(tgt);
    },
    onRangeExtendUp: () => {
      const tgt = nextFocusId(visibleIds, focusedId, -1);
      const cur = focusVisible ? focusedId : null;
      if (cur && tgt) selection.selectRange(cur, tgt);
      setFocusedId(tgt);
    },
    onSelectAll: () => selection.selectAll(),
    onOpen: () => {
      if (focusVisible && focusedId && onRowOpen) {
        const item = sorted.find((it) => getId(it) === focusedId);
        if (item) onRowOpen(item);
      }
    },
    onFocusSearch: () => searchRef.current?.focus(),
    onClear: () => {
      if (selection.count > 0) selection.clear();
      else setFocusedId(null);
    },
  });

  const selectable = !disableSelection && (bulkActions?.length ?? 0) > 0;

  const hasExpandColumn = !!expandedContent;
  const gridTemplate = React.useMemo(() => {
    const parts: string[] = [];
    if (selectable) parts.push("24px");
    if (hasExpandColumn) parts.push("24px");
    for (const c of columns) parts.push(c.width);
    if (rowActions) parts.push(rowActionsWidth);
    return parts.join(" ");
  }, [columns, selectable, rowActions, rowActionsWidth, hasExpandColumn]);

  const [expandedRows, setExpandedRows] = React.useState<Set<string>>(new Set());
  // State encoding (see expand-utils.ts): bare id = explicitly expanded,
  // `__c:` id = explicitly collapsed, absent = follow `defaultExpanded`.
  const toggleExpanded = React.useCallback(
    (id: string) => {
      setExpandedRows((prev) => nextExpandedRows(prev, id, defaultExpanded));
    },
    [defaultExpanded],
  );

  // Header state describes the whole filtered set, matching what the header
  // checkbox now selects.
  const { allSelected, someSelected } = React.useMemo(
    () => selectionHeaderState(allSelectableIds, unselectableIds, selection.selected),
    [allSelectableIds, unselectableIds, selection.selected],
  );

  function toggleAll(checked: boolean) {
    if (checked) selection.selectAll();
    else selection.clear();
  }

  const selectedItems = React.useMemo(
    () => sorted.filter((it) => selection.isSelected(getId(it))),
    [sorted, selection, getId],
  );

  // Map from id → its index in the current visible window. Used by id-keyed
  // callbacks below so they don't capture per-render `idx` closures.
  const idIndex = React.useMemo(() => {
    const map = new Map<string, number>();
    visibleIds.forEach((id, i) => map.set(id, i));
    return map;
  }, [visibleIds]);

  // Capture transient deps in refs so the id-keyed handlers stay stable
  // across renders, letting DataListRow's React.memo short-circuit.
  const selectionRef = React.useRef(selection);
  selectionRef.current = selection;
  const idIndexRef = React.useRef(idIndex);
  idIndexRef.current = idIndex;
  const onRowOpenRef = React.useRef(onRowOpen);
  onRowOpenRef.current = onRowOpen;

  const handleToggleSelectId = React.useCallback((id: string, shift: boolean) => {
    selectionRef.current.toggle(id, { shift });
  }, []);
  const handleClickId = React.useCallback((id: string) => {
    if (idIndexRef.current.has(id)) setFocusedId(id);
    // Find item by id in current sorted list, then open.
    const item = sortedRef.current.find((it) => getIdRef.current(it) === id);
    if (item) onRowOpenRef.current?.(item);
  }, []);
  const handleFocusId = React.useCallback((id: string) => {
    if (idIndexRef.current.has(id)) setFocusedId(id);
  }, []);
  const handleToggleExpandId = React.useCallback(
    (id: string) => toggleExpanded(id),
    [toggleExpanded],
  );

  const sortedRef = React.useRef(sorted);
  sortedRef.current = sorted;
  const getIdRef = React.useRef(getId);
  getIdRef.current = getId;

  function renderRow(item: T, indexHint?: number) {
    const id = getId(item);
    // Expandability is probed with the cheap `isExpandable` predicate so the
    // expanded subtree is only built for rows that are actually open.
    const expandable = expandedContent != null && (isExpandable ? isExpandable(item) : true);
    const isExpanded = expandable && isRowExpanded(expandedRows, id, defaultExpanded);
    const content = isExpanded && expandedContent ? expandedContent(item) : null;
    void indexHint;
    return (
      <DataListRow
        key={id}
        item={item}
        id={id}
        columns={columns}
        gridTemplate={gridTemplate}
        hasSelectColumn={selectable}
        selectable={selectable && !unselectableIds?.has(id)}
        selected={selection.isSelected(id)}
        focused={focusedId === id}
        density={density}
        onToggleSelectId={handleToggleSelectId}
        onClickId={handleClickId}
        onFocusId={handleFocusId}
        hasActionsColumn={!!rowActions}
        renderActions={rowActions}
        hasExpandColumn={hasExpandColumn}
        expandable={expandable}
        expanded={isExpanded}
        onToggleExpandId={handleToggleExpandId}
        expandedContent={isExpanded ? content : null}
      />
    );
  }

  const isEmpty = !loading && sorted.length === 0;
  const filtersActive = filters.length > 0 || search.length > 0;

  return (
    <div className={cn("flex w-full flex-col gap-4", className)}>
      <DataListToolbar
        searchFilter={
          <DataListSearchFilter
            search={search}
            onSearchChange={setSearch}
            facets={facets}
            filters={filters}
            onFiltersChange={(next: ActiveFilter[]) => setFilters(next)}
            placeholder={searchPlaceholder}
            inputRef={searchRef}
          />
        }
        sortOptions={sortOptions}
        sort={sort || defaultSort}
        onSortChange={(id) => setSort(id)}
        groupOptions={groupings?.map((g) => ({ id: g.id, label: g.label }))}
        group={group || defaultGroupId || "none"}
        onGroupChange={(id) => setGroup(id === "none" ? "" : id)}
        leading={toolbarLeading}
        trailing={toolbarTrailing}
      />

      {selectable &&
        bulkActions &&
        bulkActions.length > 0 &&
        bulkBarVariant === "inline" &&
        sorted.length > 0 && (
          <SelectionBar
            allChecked={allSelected}
            indeterminate={someSelected}
            onAllCheckedChange={toggleAll}
            onDeselectAll={() => selection.clear()}
            summary={
              selection.count > 0 ? `${selection.count} selected` : `Select all ${totalFiltered}`
            }
            actions={bulkActions.map((a) => (
              <Button
                key={a.id}
                size="sm"
                variant={a.variant ?? "secondary"}
                disabled={a.disabled || selection.count === 0}
                onClick={() => void a.onRun(selectedItems)}
                className="gap-1"
              >
                {a.icon}
                {a.label}
              </Button>
            ))}
          />
        )}

      {loading ? (
        <div className="overflow-hidden rounded-lg border bg-card">
          <DataListSkeleton density={density} />
        </div>
      ) : isEmpty ? (
        <DataListEmpty
          icon={emptyIcon}
          title={filtersActive ? "No matches" : emptyTitle}
          description={filtersActive ? "Try clearing or adjusting your filters." : emptyDescription}
          action={
            filtersActive ? (
              <button
                type="button"
                className="text-xs text-primary hover:underline"
                onClick={() => filtersState.clearAll()}
              >
                Clear filters
              </button>
            ) : (
              emptyAction
            )
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border bg-card">
          {showHeader && (
            <DataListHeaderRow
              columns={columns}
              gridTemplate={gridTemplate}
              selectable={selectable}
              hasExpandColumn={hasExpandColumn}
              allSelected={allSelected}
              someSelected={someSelected}
              onToggleAll={toggleAll}
              hasRowActions={!!rowActions}
            />
          )}

          {grouped ? (
            <div className="flex flex-col">
              {grouped.map((g) => {
                const isCollapsed = collapsed.has(g.key);
                return (
                  <React.Fragment key={g.key}>
                    <DataListGroupHeader
                      label={g.label}
                      count={g.items.length}
                      totalCount={groupTotals?.get(g.key)}
                      collapsed={isCollapsed}
                      onToggle={() => toggleGroup(g.key)}
                    />
                    {!isCollapsed && g.items.map((it) => renderRow(it))}
                  </React.Fragment>
                );
              })}
            </div>
          ) : (
            <div className="flex flex-col">{paged.map((it, idx) => renderRow(it, idx))}</div>
          )}
        </div>
      )}

      {paginationEnabled && (
        <DataListPagination
          total={totalFiltered}
          page={page}
          pageSize={pageSize}
          pageSizeOptions={pageSizeOptions}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
        />
      )}

      {selectable && bulkActions && bulkActions.length > 0 && bulkBarVariant === "floating" && (
        <DataListBulkBar
          count={selection.count}
          selectedItems={selectedItems}
          actions={bulkActions}
          onClear={() => selection.clear()}
        />
      )}
    </div>
  );
}

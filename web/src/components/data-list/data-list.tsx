"use client";

import * as React from "react";
import { CheckIcon, ChevronLeftIcon, ChevronRightIcon, ListChecksIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SelectionBar } from "@/components/selection-bar";
import { useIsMobile } from "@/hooks/use-mobile";
import { DataListBulkBar } from "./data-list-bulk-bar";
import { DataListCardRow } from "./data-list-card-row";
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
import { scrollMinWidth } from "./mobile-utils";
import { useListFilters } from "./use-list-filters";
import { useListHotkeys } from "./use-list-hotkeys";
import { selectionHeaderState, useListSelection } from "./use-list-selection";
import type {
  ActiveFilter,
  BulkAction,
  ColumnDef,
  DataListDensity,
  DataListMobileConfig,
  FacetDef,
  GroupByDef,
  MobileAction,
  SortOption,
} from "./types";

export function isServerPaginationTotalKnown(serverPaged: boolean, totalCount?: number): boolean {
  return !serverPaged || (typeof totalCount === "number" && Number.isFinite(totalCount));
}

export function computePaginationPages(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function shouldSnapPaginationPage(
  page: number,
  pageSize: number,
  totalCount: number | undefined,
  serverPaged: boolean,
  paginationEnabled: boolean,
): boolean {
  if (!paginationEnabled) return false;
  if (!isServerPaginationTotalKnown(serverPaged, totalCount)) return false;
  const paginationTotal = serverPaged ? totalCount! : 0;
  return page > computePaginationPages(paginationTotal, pageSize);
}

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
  /**
   * Labelled actions for the mobile card action sheet. Preferred over
   * `rowActions` on phones (icon-only buttons get auto-labelled otherwise).
   */
  mobileActions?: (item: T) => MobileAction[];
  /** Title of the mobile action sheet for a row (e.g. the item name). */
  mobileActionsTitle?: (item: T) => string;
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
  /**
   * Server-driven list total (`X-Total-Count`). When `onPaginationChange` is
   * provided, `data` is treated as the current page (no client slice). Omit or
   * pass `undefined` while the total is still loading.
   */
  totalCount?: number;
  /** Fires when page or pageSize changes — use to refetch a server page. */
  onPaginationChange?: (next: { page: number; pageSize: number }) => void;

  /** Show table-style header row above the list. Defaults true. */
  showHeader?: boolean;

  /**
   * Bulk action bar style. "inline" (default) shows a static bordered bar
   * above the list (matches show detail page). "floating" shows a centered pill.
   */
  bulkBarVariant?: "floating" | "inline";

  /**
   * Mobile rendering (`useIsMobile()`: width < lg OR coarse pointer).
   * Defaults to `{ mode: "cards" }` — stacked card rows driven by
   * `ColumnDef.mobile`. Use `{ mode: "scroll" }` for wide-by-nature lists.
   */
  mobile?: DataListMobileConfig;

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
  mobileActions,
  mobileActionsTitle,
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
  totalCount,
  onPaginationChange,
  showHeader = true,
  bulkBarVariant = "inline",
  mobile,
  className,
}: DataListProps<T>) {
  const isMobile = useIsMobile();
  const mobileMode = mobile?.mode ?? "cards";
  const cardMode = isMobile && mobileMode === "cards";
  const scrollMode = isMobile && mobileMode === "scroll";
  // Card rows hide the checkbox until the user enters select mode from the
  // toolbar (iOS-style "Select"); selection is cleared on exit.
  const [selectMode, setSelectMode] = React.useState(false);
  const paginationEnabled = !!defaultPageSize;
  const serverPaged = onPaginationChange != null;
  const totalKnown = isServerPaginationTotalKnown(serverPaged, totalCount);
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

  React.useEffect(() => {
    onPaginationChange?.({ page, pageSize });
  }, [page, pageSize, onPaginationChange]);

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
  // Select-all / empty-state counts stay page-local; the footer uses the
  // server total when it is known.
  const paginationTotal = serverPaged && totalKnown ? totalCount! : totalFiltered;

  const paged = React.useMemo(() => {
    if (!paginationEnabled || serverPaged) return sorted;
    const start = (page - 1) * pageSize;
    return sorted.slice(start, start + pageSize);
  }, [sorted, page, pageSize, paginationEnabled, serverPaged]);

  // Snap to last page if current page exceeds totals (after filter narrowing,
  // or when the server total shrinks below the current page).
  const totalPages =
    totalKnown && serverPaged
      ? computePaginationPages(totalCount!, pageSize)
      : totalKnown
        ? computePaginationPages(totalFiltered, pageSize)
        : 1;
  React.useEffect(() => {
    if (!totalKnown) return;
    if (paginationEnabled && page > totalPages) setPage(totalPages);
  }, [paginationEnabled, page, totalPages, setPage, totalKnown]);

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
  const gridTracks = React.useMemo(() => {
    const parts: string[] = [];
    if (selectable) parts.push("24px");
    if (hasExpandColumn) parts.push("24px");
    for (const c of columns) parts.push(c.width);
    if (rowActions) parts.push(rowActionsWidth);
    return parts;
  }, [columns, selectable, rowActions, rowActionsWidth, hasExpandColumn]);
  const gridTemplate = React.useMemo(() => gridTracks.join(" "), [gridTracks]);
  const scrollStyle = React.useMemo(
    () => (scrollMode ? { minWidth: mobile?.minWidth ?? scrollMinWidth(gridTracks) } : undefined),
    [scrollMode, mobile?.minWidth, gridTracks],
  );

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
    if (cardMode) {
      return (
        <DataListCardRow
          key={id}
          item={item}
          id={id}
          columns={columns}
          hasSelectColumn={selectable && selectMode}
          selectable={selectable && !unselectableIds?.has(id)}
          selected={selection.isSelected(id)}
          focused={focusedId === id}
          density={density}
          onToggleSelectId={handleToggleSelectId}
          onClickId={onRowOpen ? handleClickId : undefined}
          onFocusId={handleFocusId}
          renderActions={rowActions}
          mobileActions={mobileActions}
          sheetTitle={mobileActionsTitle?.(item)}
          expandable={expandable}
          expanded={isExpanded}
          onToggleExpandId={handleToggleExpandId}
          expandedContent={isExpanded ? content : null}
          rowIndex={indexHint}
        />
      );
    }
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
        rowIndex={indexHint}
      />
    );
  }

  const isEmpty = !loading && sorted.length === 0;
  const filtersActive = filters.length > 0 || search.length > 0;
  const visibleBodyCount = grouped
    ? grouped.reduce((n, g) => n + (collapsed.has(g.key) ? 0 : g.items.length), 0)
    : paged.length;
  const headerVisible = showHeader && !cardMode;
  const ariaRowCount = (headerVisible ? 1 : 0) + visibleBodyCount;
  const groupRowStart = React.useMemo(() => {
    if (!grouped) return null;
    const starts = new Map<string, number>();
    let next = headerVisible ? 2 : 1;
    for (const g of grouped) {
      starts.set(g.key, next);
      if (!collapsed.has(g.key)) next += g.items.length;
    }
    return starts;
  }, [grouped, collapsed, headerVisible]);

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
        trailing={
          cardMode && selectable && bulkActions && bulkActions.length > 0 && sorted.length > 0 ? (
            <>
              <Button
                variant={selectMode ? "secondary" : "outline"}
                size="icon"
                aria-pressed={selectMode}
                aria-label={selectMode ? "Done selecting" : "Select rows"}
                data-slot="select-mode-toggle"
                onClick={() => {
                  if (selectMode) selection.clear();
                  setSelectMode((v) => !v);
                }}
              >
                {selectMode ? (
                  <CheckIcon className="h-4 w-4" />
                ) : (
                  <ListChecksIcon className="h-4 w-4" />
                )}
              </Button>
              {toolbarTrailing}
            </>
          ) : (
            toolbarTrailing
          )
        }
      />

      {cardMode && selectMode && selectable && sorted.length > 0 && (
        <label className="-my-1 flex min-h-11 items-center gap-3 px-4 text-sm text-muted-foreground">
          <Checkbox
            checked={allSelected}
            indeterminate={someSelected}
            onCheckedChange={(c) => toggleAll(c === true)}
            aria-label="Select all"
          />
          <span>
            {selection.count > 0 ? `${selection.count} selected` : `Select all ${totalFiltered}`}
          </span>
        </label>
      )}

      {selectable &&
        bulkActions &&
        bulkActions.length > 0 &&
        bulkBarVariant === "inline" &&
        !cardMode &&
        sorted.length > 0 && (
          <SelectionBar
            allChecked={allSelected}
            indeterminate={someSelected}
            onAllCheckedChange={toggleAll}
            onDeselectAll={() => selection.clear()}
            summary={
              selection.count > 0 ? `${selection.count} selected` : `Select all ${totalFiltered}`
            }
            hideActions={isMobile}
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
        <div
          className={cn(
            "overflow-hidden rounded-lg border bg-card",
            scrollMode && "overflow-x-auto overscroll-x-contain",
          )}
          role="grid"
          aria-label="Results"
          aria-rowcount={ariaRowCount}
          style={scrollStyle}
        >
          {headerVisible && (
            <div role="rowgroup">
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
            </div>
          )}

          {grouped ? (
            <div role="rowgroup" className="flex flex-col">
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
                      className={cn(!headerVisible && "top-0", cardMode && "h-10 px-4")}
                    />
                    {!isCollapsed &&
                      g.items.map((it, idx) =>
                        renderRow(it, (groupRowStart?.get(g.key) ?? 1) + idx),
                      )}
                  </React.Fragment>
                );
              })}
            </div>
          ) : (
            <div role="rowgroup" className="flex flex-col">
              {paged.map((it, idx) => renderRow(it, (headerVisible ? 2 : 1) + idx))}
            </div>
          )}
        </div>
      )}

      {paginationEnabled &&
        (totalKnown ? (
          <DataListPagination
            total={paginationTotal}
            page={page}
            pageSize={pageSize}
            pageSizeOptions={pageSizeOptions}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        ) : (
          <div
            className={cn(
              "grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-1 text-xs text-muted-foreground",
            )}
          >
            <div className="justify-self-start tabular-nums">
              Page <span className="font-medium text-foreground">{page}</span>
            </div>
            <div className="flex items-center gap-1 justify-self-center">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
                aria-label="Previous page"
              >
                <ChevronLeftIcon className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                disabled={totalFiltered < pageSize}
                onClick={() => setPage(page + 1)}
                aria-label="Next page"
              >
                <ChevronRightIcon className="h-4 w-4" />
              </Button>
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="ghost" size="sm" className="h-7 gap-1 justify-self-end text-xs">
                    Items:{" "}
                    <span className="font-medium text-foreground tabular-nums">{pageSize}</span>
                  </Button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuRadioGroup
                    value={String(pageSize)}
                    onValueChange={(v) => setPageSize(Number(v))}
                  >
                    {pageSizeOptions.map((n) => (
                      <DropdownMenuRadioItem key={n} value={String(n)}>
                        {n} per page
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ))}

      {selectable &&
        bulkActions &&
        bulkActions.length > 0 &&
        (bulkBarVariant === "floating" || isMobile) && (
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

"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/hooks/use-mobile";
import { DataListCardRow } from "./data-list-card-row";
import { DataListEmpty } from "./data-list-empty";
import { DataListHeaderRow, DataListRow } from "./data-list-row";
import { isRowExpanded, nextExpandedRows } from "./expand-utils";
import { scrollMinWidth } from "./mobile-utils";
import type { ColumnDef, DataListDensity, DataListMobileConfig, MobileAction } from "./types";

export interface DataListSectionProps<T> {
  data: T[];
  getId: (item: T) => string;
  columns: ColumnDef<T>[];
  /** Optional checkbox column + selection state. */
  selectable?: boolean;
  selectedIds?: Set<string>;
  onToggleSelected?: (id: string, shift: boolean) => void;
  onToggleAllSelected?: (checked: boolean) => void;
  unselectableIds?: Set<string>;
  /** Show table-style header. Default true. */
  showHeader?: boolean;
  density?: DataListDensity;
  rowActions?: (item: T) => React.ReactNode;
  rowActionsWidth?: string;
  /** Labelled actions for the mobile card action sheet (see `DataListProps`). */
  mobileActions?: (item: T) => MobileAction[];
  mobileActionsTitle?: (item: T) => string;
  /** Extra classes per mobile card row (e.g. to emphasise group/parent rows). */
  mobileRowClassName?: (item: T) => string | undefined;
  /** Mobile only: show the selection checkbox column. Defaults to `selectable`. */
  mobileShowSelect?: boolean;
  expandedContent?: (item: T) => React.ReactNode | null;
  /**
   * Cheap predicate for whether a row can expand. Defaults to true when
   * `expandedContent` is provided. Pass this whenever `expandedContent` can
   * return null for some rows, or whenever the expanded tree is nontrivial —
   * it lets collapsed rows skip building that tree entirely.
   */
  isExpandable?: (item: T) => boolean;
  defaultExpanded?: boolean;
  /** External expand control. If provided, replaces internal state. */
  expandedIds?: Set<string>;
  onToggleExpanded?: (id: string) => void;
  onRowOpen?: (item: T) => void;
  /** Empty state when data is []. Pass null to render nothing. */
  emptyTitle?: React.ReactNode;
  emptyDescription?: React.ReactNode;
  emptyIcon?: React.ReactNode;
  /** Card wrapper around rows. Default true. */
  bordered?: boolean;
  /** Indent rows for nested usage (in pixels). */
  indent?: number;
  /** Mobile rendering; see `DataListProps.mobile`. Defaults to card rows. */
  mobile?: DataListMobileConfig;
  className?: string;
}

/**
 * Headless rows-only renderer that shares DataList's row visuals.
 * Use inside `expandedContent` for nested tree rendering, or in custom
 * layouts where you don't want a full toolbar / filter / pagination.
 */
export function DataListSection<T>({
  data,
  getId,
  columns,
  selectable,
  selectedIds,
  onToggleSelected,
  onToggleAllSelected,
  unselectableIds,
  showHeader = true,
  density = "standard",
  rowActions,
  rowActionsWidth = "88px",
  mobileActions,
  mobileActionsTitle,
  mobileRowClassName,
  mobileShowSelect,
  expandedContent,
  isExpandable,
  defaultExpanded = false,
  expandedIds,
  onToggleExpanded,
  onRowOpen,
  emptyTitle,
  emptyDescription,
  emptyIcon,
  bordered = true,
  indent = 0,
  mobile,
  className,
}: DataListSectionProps<T>) {
  const hasExpandColumn = !!expandedContent;
  const isMobile = useIsMobile();
  const mobileMode = mobile?.mode ?? "cards";
  const cardMode = isMobile && mobileMode === "cards";
  const scrollMode = isMobile && mobileMode === "scroll";

  const gridTracks = React.useMemo(() => {
    const parts: string[] = [];
    if (selectable) parts.push("24px");
    if (hasExpandColumn) parts.push("24px");
    for (const c of columns) parts.push(c.width);
    if (rowActions) parts.push(rowActionsWidth);
    return parts;
  }, [columns, selectable, rowActions, rowActionsWidth, hasExpandColumn]);
  const gridTemplate = React.useMemo(() => gridTracks.join(" "), [gridTracks]);

  const [internalExpanded, setInternalExpanded] = React.useState<Set<string>>(new Set());
  const expandedRows = expandedIds ?? internalExpanded;
  // State encoding (see expand-utils.ts): bare id = explicitly expanded,
  // `__c:` id = explicitly collapsed, absent = follow `defaultExpanded`.
  // Identical semantics to DataList — keep the two in sync.
  const toggleExpanded = React.useCallback(
    (id: string) => {
      if (onToggleExpanded) {
        onToggleExpanded(id);
        return;
      }
      setInternalExpanded((prev) => nextExpandedRows(prev, id, defaultExpanded));
    },
    [onToggleExpanded, defaultExpanded],
  );

  // Stable id-keyed callbacks → React.memo on DataListRow short-circuits when
  // sibling rows change.
  const onToggleSelectedRef = React.useRef(onToggleSelected);
  onToggleSelectedRef.current = onToggleSelected;
  const onRowOpenRef = React.useRef(onRowOpen);
  onRowOpenRef.current = onRowOpen;

  const handleToggleSelectId = React.useCallback((id: string, shift: boolean) => {
    onToggleSelectedRef.current?.(id, shift);
  }, []);
  const handleClickId = React.useCallback((id: string) => {
    const item = dataRef.current.find((it) => getIdRef.current(it) === id);
    if (item) onRowOpenRef.current?.(item);
  }, []);
  const handleToggleExpandId = React.useCallback(
    (id: string) => toggleExpanded(id),
    [toggleExpanded],
  );

  const dataRef = React.useRef(data);
  dataRef.current = data;
  const getIdRef = React.useRef(getId);
  getIdRef.current = getId;

  // Header summary — single pass instead of two `every` + `some` + `every`.
  const { allSelected, someSelected } = React.useMemo(() => {
    if (!selectable || data.length === 0 || !selectedIds) {
      return { allSelected: false, someSelected: false };
    }
    let selected = 0;
    for (const it of data) if (selectedIds.has(getId(it))) selected++;
    return {
      allSelected: selected === data.length,
      someSelected: selected > 0 && selected < data.length,
    };
  }, [selectable, data, selectedIds, getId]);

  if (data.length === 0) {
    if (emptyTitle == null) return null;
    return (
      <DataListEmpty
        icon={emptyIcon}
        title={emptyTitle}
        description={emptyDescription}
        className={bordered ? "" : "border-0 py-6"}
      />
    );
  }

  return (
    <div
      className={cn(
        bordered && "overflow-hidden rounded-lg border bg-card",
        scrollMode && "overflow-x-auto overscroll-x-contain",
        className,
      )}
      style={{
        marginLeft: indent > 0 ? indent : undefined,
        minWidth: scrollMode ? (mobile?.minWidth ?? scrollMinWidth(gridTracks)) : undefined,
      }}
    >
      {showHeader && !cardMode && (
        <DataListHeaderRow
          columns={columns}
          gridTemplate={gridTemplate}
          selectable={!!selectable}
          hasExpandColumn={hasExpandColumn}
          allSelected={allSelected}
          someSelected={someSelected}
          onToggleAll={(checked) => onToggleAllSelected?.(checked)}
          hasRowActions={!!rowActions}
        />
      )}
      <div className="flex flex-col">
        {data.map((item) => {
          const id = getId(item);
          // With `isExpandable` the expanded subtree is built only for rows
          // that are actually open. Without a predicate we can't know whether
          // a row expands without calling `expandedContent` (it may return
          // null per item), so that path keeps the compute-first behaviour.
          let expandable: boolean;
          let content: React.ReactNode | null = null;
          if (isExpandable) {
            expandable = expandedContent != null && isExpandable(item);
          } else {
            content = expandedContent ? expandedContent(item) : null;
            expandable = content != null;
          }
          const isExpanded = expandable && isRowExpanded(expandedRows, id, defaultExpanded);
          if (isExpanded && isExpandable && expandedContent) content = expandedContent(item);
          const isSelectable = !!selectable && !(unselectableIds?.has(id) ?? false);
          if (cardMode) {
            return (
              <DataListCardRow<T>
                key={id}
                item={item}
                id={id}
                columns={columns}
                hasSelectColumn={mobileShowSelect ?? !!selectable}
                selectable={isSelectable}
                selected={!!selectedIds?.has(id)}
                focused={false}
                density={density}
                onToggleSelectId={handleToggleSelectId}
                onClickId={onRowOpen ? handleClickId : undefined}
                renderActions={rowActions}
                mobileActions={mobileActions}
                sheetTitle={mobileActionsTitle?.(item)}
                className={mobileRowClassName?.(item)}
                expandable={expandable}
                expanded={isExpanded}
                onToggleExpandId={handleToggleExpandId}
                expandedContent={isExpanded ? content : null}
              />
            );
          }
          return (
            <DataListRow<T>
              key={id}
              item={item}
              id={id}
              columns={columns}
              gridTemplate={gridTemplate}
              hasSelectColumn={!!selectable}
              selectable={isSelectable}
              selected={!!selectedIds?.has(id)}
              focused={false}
              density={density}
              onToggleSelectId={handleToggleSelectId}
              onClickId={handleClickId}
              hasActionsColumn={!!rowActions}
              renderActions={rowActions}
              hasExpandColumn={hasExpandColumn}
              expandable={expandable}
              expanded={isExpanded}
              onToggleExpandId={handleToggleExpandId}
              expandedContent={isExpanded ? content : null}
            />
          );
        })}
      </div>
    </div>
  );
}

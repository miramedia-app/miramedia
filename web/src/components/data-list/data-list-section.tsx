"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { DataListEmpty } from "./data-list-empty";
import { DataListHeaderRow, DataListRow } from "./data-list-row";
import type { ColumnDef, DataListDensity } from "./types";

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
  expandedContent?: (item: T) => React.ReactNode | null;
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
  expandedContent,
  defaultExpanded = false,
  expandedIds,
  onToggleExpanded,
  onRowOpen,
  emptyTitle,
  emptyDescription,
  emptyIcon,
  bordered = true,
  indent = 0,
  className,
}: DataListSectionProps<T>) {
  const hasExpandColumn = !!expandedContent;

  const gridTemplate = React.useMemo(() => {
    const parts: string[] = [];
    if (selectable) parts.push("24px");
    if (hasExpandColumn) parts.push("24px");
    for (const c of columns) parts.push(c.width);
    if (rowActions) parts.push(rowActionsWidth);
    return parts.join(" ");
  }, [columns, selectable, rowActions, rowActionsWidth, hasExpandColumn]);

  const [internalExpanded, setInternalExpanded] = React.useState<Set<string>>(new Set());
  const expandedRows = expandedIds ?? internalExpanded;
  const toggleExpanded = React.useCallback(
    (id: string) => {
      if (onToggleExpanded) {
        onToggleExpanded(id);
        return;
      }
      setInternalExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    },
    [onToggleExpanded],
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
      className={cn(bordered && "overflow-hidden rounded-lg border bg-card", className)}
      style={indent > 0 ? { marginLeft: indent } : undefined}
    >
      {showHeader && (
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
          // Compute once — was called twice (probe + render) before.
          const content = expandedContent ? expandedContent(item) : null;
          const expandable = content != null;
          const isExpanded =
            expandable &&
            (expandedRows.has(id) || (defaultExpanded && !expandedRows.has(`__c:${id}`)));
          const isSelectable = !!selectable && !(unselectableIds?.has(id) ?? false);
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

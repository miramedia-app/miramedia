"use client";

import * as React from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import type { ColumnDef, DataListDensity } from "./types";

export interface DataListRowProps<T> {
  item: T;
  /** Stable row identifier — forwarded to id-keyed callbacks. */
  id: string;
  columns: ColumnDef<T>[];
  /** Grid-template-columns string built from column widths + leading slots. */
  gridTemplate: string;
  /** True when grid reserves a checkbox column. */
  hasSelectColumn: boolean;
  /** True when this specific row may toggle selection. */
  selectable: boolean;
  selected: boolean;
  focused: boolean;
  density: DataListDensity;
  /** Id-keyed callbacks kept stable in the parent so React.memo can short-circuit. */
  onToggleSelectId?: (id: string, shift: boolean) => void;
  onClickId?: (id: string) => void;
  onFocusId?: (id: string) => void;
  onToggleExpandId?: (id: string) => void;
  /** Render function for trailing actions cell. Stable per parent. */
  renderActions?: (item: T) => React.ReactNode;
  /** True when grid reserves a trailing actions column. */
  hasActionsColumn?: boolean;
  /** True when grid reserves an expand chevron column. */
  hasExpandColumn?: boolean;
  /** True when this row is expandable. */
  expandable?: boolean;
  /** Current expand state of the row. */
  expanded?: boolean;
  /** Free-form content rendered below the row when expanded. */
  expandedContent?: React.ReactNode;
  /** 1-based ARIA row index when this row sits inside a grid. */
  rowIndex?: number;
  className?: string;
}

const HIDE_BELOW: Record<NonNullable<ColumnDef<unknown>["hideBelow"]>, string> = {
  sm: "hidden sm:flex",
  md: "hidden md:flex",
  lg: "hidden lg:flex",
  xl: "hidden xl:flex",
};

function DataListRowImpl<T>({
  item,
  id,
  columns,
  gridTemplate,
  hasSelectColumn,
  selectable,
  selected,
  focused,
  density,
  onToggleSelectId,
  onClickId,
  onFocusId,
  onToggleExpandId,
  renderActions,
  hasActionsColumn,
  hasExpandColumn,
  expandable,
  expanded,
  expandedContent,
  rowIndex,
  className,
}: DataListRowProps<T>) {
  const minH =
    density === "rich"
      ? "min-h-14 py-2"
      : density === "compact"
        ? "min-h-9 text-[13px]"
        : "min-h-11";

  // Intrinsic height for content-visibility: matches the min-h above so
  // off-screen placeholders don't jump scroll position when entering view.
  const intrinsicPx = density === "rich" ? 56 : density === "compact" ? 36 : 44;

  return (
    <div
      className="border-b border-border/40 last:border-b-0"
      style={{
        contentVisibility: "auto",
        containIntrinsicSize: `auto ${intrinsicPx}px`,
      }}
    >
      <div
        role="row"
        tabIndex={-1}
        aria-rowindex={rowIndex}
        data-selected={selected ? "" : undefined}
        data-focused={focused ? "" : undefined}
        onClick={() => onClickId?.(id)}
        onFocus={() => onFocusId?.(id)}
        className={cn(
          "group relative grid items-center gap-x-2 px-3 transition-colors",
          minH,
          "hover:bg-muted/40",
          selected && "bg-primary/8",
          focused && "bg-muted/60",
          (selected || focused) &&
            "before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-primary",
          className,
        )}
        style={{ gridTemplateColumns: gridTemplate }}
      >
        {hasSelectColumn && (
          <div className="flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
            {selectable ? (
              <Checkbox
                checked={selected}
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleSelectId?.(id, (e as React.MouseEvent).shiftKey);
                }}
                onCheckedChange={() => {}}
                aria-label="Select row"
              />
            ) : (
              <span className="h-4 w-4" aria-hidden />
            )}
          </div>
        )}
        {hasExpandColumn && (
          <div className="flex items-center justify-center">
            {expandable ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleExpandId?.(id);
                }}
                className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                aria-label={expanded ? "Collapse row" : "Expand row"}
                aria-expanded={expanded}
              >
                {expanded ? (
                  <ChevronDownIcon className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRightIcon className="h-3.5 w-3.5" />
                )}
              </button>
            ) : (
              <span className="h-4 w-4" aria-hidden />
            )}
          </div>
        )}
        {columns.map((col) => (
          <div
            key={col.id}
            role="cell"
            className={cn(
              "flex min-w-0 items-center gap-2",
              col.align === "end" && "justify-end text-end",
              col.align === "center" && "justify-center text-center",
              col.mono && "tabular-nums",
              col.hideBelow && HIDE_BELOW[col.hideBelow],
            )}
          >
            {col.render(item, { focused, selected })}
          </div>
        ))}
        {hasActionsColumn && (
          <div
            className="flex items-center justify-end gap-0.5"
            onClick={(e) => e.stopPropagation()}
          >
            {renderActions?.(item)}
          </div>
        )}
      </div>
      {expanded && expandedContent ? (
        <div className="bg-muted/30" onClick={(e) => e.stopPropagation()}>
          {expandedContent}
        </div>
      ) : null}
    </div>
  );
}

// Memo with default shallow compare. All callback props are id-keyed and
// kept stable in the parent (see data-list.tsx); rowActions is rendered
// lazily inside this row from the stable renderActions function.
export const DataListRow = React.memo(DataListRowImpl) as typeof DataListRowImpl;

export interface DataListHeaderRowProps<T> {
  columns: ColumnDef<T>[];
  gridTemplate: string;
  selectable: boolean;
  hasExpandColumn?: boolean;
  allSelected: boolean;
  someSelected: boolean;
  onToggleAll: (checked: boolean) => void;
  hasRowActions: boolean;
}

export function DataListHeaderRow<T>({
  columns,
  gridTemplate,
  selectable,
  hasExpandColumn,
  allSelected,
  someSelected,
  onToggleAll,
  hasRowActions,
}: DataListHeaderRowProps<T>) {
  return (
    <div
      role="row"
      aria-rowindex={1}
      className="sticky top-0 z-10 grid h-9 items-center gap-x-2 border-b bg-background/95 px-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase backdrop-blur"
      style={{ gridTemplateColumns: gridTemplate }}
    >
      {selectable && (
        <div className="flex items-center justify-center">
          <Checkbox
            checked={allSelected}
            indeterminate={someSelected && !allSelected}
            onCheckedChange={(c) => onToggleAll(c === true)}
            aria-label="Select all"
          />
        </div>
      )}
      {hasExpandColumn && <div />}
      {columns.map((col) => (
        <div
          key={col.id}
          className={cn(
            "truncate",
            col.align === "end" && "text-end",
            col.align === "center" && "text-center",
            col.hideBelow && HIDE_BELOW[col.hideBelow],
          )}
        >
          {col.header ?? null}
        </div>
      ))}
      {hasRowActions && <div />}
    </div>
  );
}

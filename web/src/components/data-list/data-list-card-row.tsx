"use client";

import * as React from "react";
import { ChevronDownIcon, ChevronRightIcon, MoreHorizontalIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { cn } from "@/lib/utils";
import {
  actionNodeLabel,
  flattenActionNodes,
  isPrimaryActionNode,
  renderMobileCell,
  resolveMobileLayout,
} from "./mobile-utils";
import type { ColumnDef, DataListDensity, MobileAction } from "./types";

/**
 * A single action stays inline; anything more collapses into one `⋯` button
 * that opens a bottom action sheet. A `primary` action (`MobileAction.primary`
 * or a `MobilePrimaryAction`-wrapped node) is always inlined at 44px, with the
 * rest behind a compact `⋯` beside it.
 */
export const MAX_INLINE_ACTIONS = 1;

export interface DataListCardRowProps<T> {
  item: T;
  id: string;
  columns: ColumnDef<T>[];
  hasSelectColumn: boolean;
  selectable: boolean;
  selected: boolean;
  focused: boolean;
  density: DataListDensity;
  onToggleSelectId?: (id: string, shift: boolean) => void;
  onClickId?: (id: string) => void;
  onFocusId?: (id: string) => void;
  onToggleExpandId?: (id: string) => void;
  renderActions?: (item: T) => React.ReactNode;
  /**
   * Labelled actions for the mobile action sheet. When provided it replaces
   * `renderActions` on the card; otherwise the desktop action nodes are
   * flattened into the sheet and labelled from their `title` / `aria-label`.
   */
  mobileActions?: (item: T) => MobileAction[];
  expandable?: boolean;
  expanded?: boolean;
  expandedContent?: React.ReactNode;
  rowIndex?: number;
  /** Accessible name for the action sheet (defaults to "Actions"). */
  sheetTitle?: string;
  className?: string;
}

function OverflowButton({ onClick, compact }: { onClick: () => void; compact?: boolean }) {
  return (
    <Button
      variant="ghost"
      size={compact ? "icon-sm" : "icon"}
      aria-label="More actions"
      data-slot="card-actions-overflow"
      className={cn("text-muted-foreground", compact ? "h-9 w-9" : "h-11 w-11")}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <MoreHorizontalIcon className={compact ? "h-4 w-4" : "h-5 w-5"} />
    </Button>
  );
}

function PrimaryActionButton({ action }: { action: MobileAction }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={action.label}
      title={action.label}
      disabled={action.disabled}
      data-slot="card-actions-primary"
      className={cn("h-11 w-11", action.destructive ? "text-destructive" : "text-foreground")}
      onClick={(e) => {
        e.stopPropagation();
        action.onSelect();
      }}
    >
      <span className="flex items-center justify-center [&>svg]:h-5 [&>svg]:w-5">
        {action.icon}
      </span>
    </Button>
  );
}

/** Bottom sheet listing labelled actions as full-width 48px rows. */
function ActionSheet({
  open,
  onOpenChange,
  title,
  actions,
  nodes,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  actions: MobileAction[] | null;
  nodes: React.ReactNode[];
}) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="pb-safe-b" onClick={(e) => e.stopPropagation()}>
        <DrawerHeader className="text-left">
          <DrawerTitle className="truncate">{title}</DrawerTitle>
          <DrawerDescription className="sr-only">Row actions</DrawerDescription>
        </DrawerHeader>
        <div className="flex flex-col gap-1 px-2 pb-3" data-slot="card-action-sheet">
          {actions
            ? actions.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  disabled={a.disabled}
                  onClick={() => {
                    onOpenChange(false);
                    a.onSelect();
                  }}
                  className={cn(
                    "flex min-h-12 w-full items-center gap-3 rounded-md px-3 text-left text-sm hover:bg-muted active:bg-muted disabled:opacity-50",
                    a.destructive && "text-destructive",
                  )}
                >
                  {a.icon ? (
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center [&>svg]:h-4 [&>svg]:w-4">
                      {a.icon}
                    </span>
                  ) : null}
                  <span className="min-w-0 flex-1 truncate">{a.label}</span>
                </button>
              ))
            : nodes.map((n, i) => {
                const label = actionNodeLabel(n);
                if (label && React.isValidElement(n)) {
                  // Icon-only desktop button → full-width labelled row.
                  const el = n as React.ReactElement<{
                    className?: string;
                    children?: React.ReactNode;
                    size?: string;
                  }>;
                  return React.cloneElement(el, {
                    key: i,
                    size: "default",
                    className: cn(
                      el.props.className,
                      "h-12 w-full justify-start gap-3 px-3 text-sm text-foreground [&>svg]:h-4 [&>svg]:w-4",
                    ),
                    children: (
                      <>
                        {el.props.children}
                        <span className="min-w-0 flex-1 truncate text-left">{label}</span>
                      </>
                    ),
                  });
                }
                return (
                  <div
                    key={i}
                    className="flex min-h-12 items-center px-3 [&>button]:h-11 [&>button]:min-w-11"
                  >
                    {n}
                  </div>
                );
              })}
        </div>
      </DrawerContent>
    </Drawer>
  );
}

function DataListCardRowImpl<T>({
  item,
  id,
  columns,
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
  mobileActions,
  expandable,
  expanded,
  expandedContent,
  rowIndex,
  sheetTitle,
  className,
}: DataListCardRowProps<T>) {
  const layout = React.useMemo(() => resolveMobileLayout(columns), [columns]);
  const ctx = { focused, selected };
  const [sheetOpen, setSheetOpen] = React.useState(false);

  const actions = React.useMemo(
    () => (mobileActions ? mobileActions(item).filter(Boolean) : null),
    [mobileActions, item],
  );
  const allNodes = React.useMemo(
    () => (!actions && renderActions ? flattenActionNodes(renderActions(item)) : []),
    [actions, renderActions, item],
  );
  // Primary action: inline at the right edge; everything else goes to the sheet.
  const primaryAction = actions?.find((a) => a.primary) ?? null;
  const sheetActions = actions ? actions.filter((a) => a !== primaryAction) : null;
  const primaryNode = allNodes.find(isPrimaryActionNode) ?? null;
  const actionNodes = primaryNode ? allNodes.filter((n) => n !== primaryNode) : allNodes;
  const hasPrimary = !!primaryAction || !!primaryNode;
  const hasSheet = actions
    ? (sheetActions?.length ?? 0) > 0
    : hasPrimary
      ? actionNodes.length > 0
      : actionNodes.length > MAX_INLINE_ACTIONS;
  const inlineNodes =
    !actions && !hasPrimary && actionNodes.length <= MAX_INLINE_ACTIONS ? actionNodes : [];

  const notEmpty = (n: React.ReactNode) => n != null && n !== false && n !== "";
  const titleNode = layout.title ? renderMobileCell(layout.title, item, ctx) : null;
  const subtitleNode = layout.subtitle ? renderMobileCell(layout.subtitle, item, ctx) : null;
  const statusNode = layout.status ? renderMobileCell(layout.status, item, ctx) : null;
  const progressNode = layout.progress ? renderMobileCell(layout.progress, item, ctx) : null;
  const metaCells = layout.meta
    .map((col) => ({ col, node: renderMobileCell(col, item, ctx) }))
    .filter((m) => notEmpty(m.node));

  const tappable = !!expandable || !!onClickId;
  const handleRowClick = () => {
    if (expandable) onToggleExpandId?.(id);
    else onClickId?.(id);
  };

  return (
    <div
      className="border-b border-border/50 last:border-b-0"
      style={{ contentVisibility: "auto", containIntrinsicSize: "auto 72px" }}
    >
      <div
        role="row"
        tabIndex={-1}
        aria-rowindex={rowIndex}
        data-selected={selected ? "" : undefined}
        data-focused={focused ? "" : undefined}
        data-slot="card-row"
        onClick={handleRowClick}
        onFocus={() => onFocusId?.(id)}
        className={cn(
          "group relative flex min-h-16 items-center gap-2 py-3 pr-2 pl-4 transition-colors",
          density === "compact" && "min-h-14 py-2.5",
          tappable && "cursor-pointer active:bg-muted/50",
          selected && "bg-primary/8",
          focused && "bg-muted/60",
          (selected || focused) &&
            "before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-primary",
          className,
        )}
      >
        {hasSelectColumn && (
          <div
            className="-ml-2 flex min-h-11 min-w-11 shrink-0 items-center justify-center"
            onClick={(e) => e.stopPropagation()}
          >
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

        <div className="flex min-w-0 flex-1 flex-col gap-1" role="cell">
          <div className="flex min-w-0 items-start gap-2">
            {notEmpty(titleNode) && (
              <div
                className={cn(
                  "min-w-0 flex-1 text-[15px] leading-snug font-medium",
                  "[&_a]:font-medium [&_span]:text-inherit",
                  layout.title?.mono && "tabular-nums",
                )}
                data-slot="card-title"
              >
                {titleNode}
              </div>
            )}
            {notEmpty(statusNode) && (
              <div
                className="flex shrink-0 items-center pt-px [&>*]:shrink-0"
                data-slot="card-status"
              >
                {statusNode}
              </div>
            )}
          </div>
          {notEmpty(subtitleNode) && (
            <div
              className={cn(
                "flex min-w-0 items-center gap-2 text-xs leading-snug text-muted-foreground",
                layout.subtitle?.mono && "tabular-nums",
              )}
              data-slot="card-subtitle"
            >
              {subtitleNode}
            </div>
          )}
          {notEmpty(progressNode) && (
            <div className="w-full pt-0.5 [&>*]:pr-0" data-slot="card-progress">
              {progressNode}
            </div>
          )}
          {metaCells.length > 0 && (
            <div
              className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 pt-0.5 text-xs text-muted-foreground"
              data-slot="card-meta"
            >
              {metaCells.map(({ col, node }, i) => (
                <React.Fragment key={col.id}>
                  {i > 0 && (
                    <span aria-hidden className="text-muted-foreground/50 select-none">
                      ·
                    </span>
                  )}
                  <span
                    data-column={col.id}
                    className={cn(
                      "inline-flex max-w-full min-w-0 items-center gap-1",
                      col.mono && "tabular-nums",
                    )}
                  >
                    {node}
                  </span>
                </React.Fragment>
              ))}
            </div>
          )}
        </div>

        {inlineNodes.length > 0 && (
          <div
            className="flex shrink-0 items-center [&>button]:h-11 [&>button]:w-11 [&>button]:text-muted-foreground"
            data-slot="card-actions"
            onClick={(e) => e.stopPropagation()}
          >
            {inlineNodes.map((n, i) => (
              <React.Fragment key={i}>{n}</React.Fragment>
            ))}
          </div>
        )}
        {(hasPrimary || hasSheet) && (
          <div
            className={cn(
              "flex shrink-0 items-center",
              hasPrimary && "gap-0.5 [&>button]:h-11 [&>button]:w-11 [&>button]:text-foreground",
            )}
            data-slot="card-actions"
            onClick={(e) => e.stopPropagation()}
          >
            {primaryAction ? <PrimaryActionButton action={primaryAction} /> : primaryNode}
            {hasSheet && <OverflowButton compact={hasPrimary} onClick={() => setSheetOpen(true)} />}
          </div>
        )}

        {expandable ? (
          <span
            className="flex h-11 w-8 shrink-0 items-center justify-center text-muted-foreground"
            aria-hidden
          >
            {expanded ? (
              <ChevronDownIcon className="h-4 w-4" />
            ) : (
              <ChevronRightIcon className="h-4 w-4" />
            )}
          </span>
        ) : onClickId && !hasSheet && inlineNodes.length === 0 ? (
          <span
            className="flex h-11 w-8 shrink-0 items-center justify-center text-muted-foreground/60"
            aria-hidden
          >
            <ChevronRightIcon className="h-4 w-4" />
          </span>
        ) : null}
      </div>
      {expanded && expandedContent ? (
        <div className="bg-muted/30" onClick={(e) => e.stopPropagation()}>
          {expandedContent}
        </div>
      ) : null}
      {hasSheet && (
        <ActionSheet
          open={sheetOpen}
          onOpenChange={setSheetOpen}
          title={sheetTitle ?? "Actions"}
          actions={sheetActions && sheetActions.length > 0 ? sheetActions : null}
          nodes={actionNodes}
        />
      )}
    </div>
  );
}

export const DataListCardRow = React.memo(DataListCardRowImpl) as typeof DataListCardRowImpl;

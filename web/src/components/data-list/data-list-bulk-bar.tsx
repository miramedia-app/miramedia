"use client";

import * as React from "react";
import { MoreHorizontalIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import type { BulkAction } from "./types";

/** Actions kept inline on the mobile bottom bar before overflowing to `⋯`. */
export const MOBILE_INLINE_BULK_ACTIONS = 2;

export function splitBulkActions<T>(
  actions: BulkAction<T>[],
  inline: number,
): { inline: BulkAction<T>[]; overflow: BulkAction<T>[] } {
  if (actions.length <= inline) return { inline: actions, overflow: [] };
  return { inline: actions.slice(0, inline), overflow: actions.slice(inline) };
}

export interface DataListBulkBarProps<T> {
  count: number;
  selectedItems: T[];
  actions: BulkAction<T>[];
  onClear: () => void;
  className?: string;
}

/**
 * Desktop: centered floating pill. Mobile (`useIsMobile`): full-width bottom
 * bar that sits above the tab bar (`bottom-14`) and the home indicator
 * (`pb-safe-b`); actions beyond the first two collapse into a `⋯` menu.
 */
export function DataListBulkBar<T>({
  count,
  selectedItems,
  actions,
  onClear,
  className,
}: DataListBulkBarProps<T>) {
  const isMobile = useIsMobile();
  if (count === 0) return null;

  if (isMobile) {
    const { inline, overflow } = splitBulkActions(actions, MOBILE_INLINE_BULK_ACTIONS);
    return (
      <div
        role="toolbar"
        aria-label="Bulk actions"
        data-slot="bulk-bar-mobile"
        className={cn(
          "fixed inset-x-0 bottom-14 z-40 flex flex-wrap items-center gap-1 border-t bg-popover px-2 py-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] text-sm shadow-lg lg:bottom-4",
          "animate-in duration-150 fade-in-0 slide-in-from-bottom-2",
          className,
        )}
      >
        <span className="px-2 text-xs font-medium text-muted-foreground tabular-nums">
          {count} selected
        </span>
        <div className="ml-auto flex items-center gap-1">
          {inline.map((a) => (
            <Button
              key={a.id}
              size="sm"
              variant={a.variant ?? "ghost"}
              disabled={a.disabled}
              onClick={() => void a.onRun(selectedItems)}
              className="gap-1 text-xs"
            >
              {a.icon}
              {a.label}
            </Button>
          ))}
          {overflow.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button size="icon" variant="ghost" aria-label="More bulk actions" />}
              >
                <MoreHorizontalIcon className="h-4 w-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" side="top">
                {overflow.map((a) => (
                  <DropdownMenuItem
                    key={a.id}
                    disabled={a.disabled}
                    variant={a.variant === "destructive" ? "destructive" : "default"}
                    onClick={() => void a.onRun(selectedItems)}
                  >
                    {a.icon}
                    {a.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          <Button size="icon" variant="ghost" onClick={onClear} aria-label="Clear selection">
            <XIcon className="h-4 w-4" />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      role="toolbar"
      aria-label="Bulk actions"
      className={cn(
        "fixed bottom-4 left-1/2 z-40 flex h-11 max-w-[calc(100vw-1rem)] -translate-x-1/2 items-center gap-1 overflow-x-auto rounded-full border bg-popover px-2 text-sm shadow-lg ring-1 ring-foreground/10",
        "animate-in duration-150 fade-in-0 slide-in-from-bottom-2",
        className,
      )}
    >
      <span className="px-2 text-xs font-medium whitespace-nowrap text-muted-foreground tabular-nums">
        {count} selected
      </span>
      <span className="mx-0.5 h-5 w-px bg-border" />
      {actions.map((a) => (
        <Button
          key={a.id}
          size="sm"
          variant={a.variant ?? "ghost"}
          disabled={a.disabled}
          onClick={() => void a.onRun(selectedItems)}
          className="h-8 gap-1 text-xs"
        >
          {a.icon}
          {a.label}
        </Button>
      ))}
      <span className="mx-0.5 h-5 w-px bg-border" />
      <Button
        size="icon"
        variant="ghost"
        className="h-7 w-7"
        onClick={onClear}
        aria-label="Clear selection"
      >
        <XIcon className="h-4 w-4" />
      </Button>
    </div>
  );
}

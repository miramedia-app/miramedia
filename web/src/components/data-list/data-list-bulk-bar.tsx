"use client";

import * as React from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { BulkAction } from "./types";

export interface DataListBulkBarProps<T> {
  count: number;
  selectedItems: T[];
  actions: BulkAction<T>[];
  onClear: () => void;
  className?: string;
}

export function DataListBulkBar<T>({
  count,
  selectedItems,
  actions,
  onClear,
  className,
}: DataListBulkBarProps<T>) {
  if (count === 0) return null;
  return (
    <div
      role="toolbar"
      className={cn(
        "fixed bottom-4 left-1/2 z-40 flex h-11 -translate-x-1/2 items-center gap-1 rounded-full border bg-popover px-2 text-sm shadow-lg ring-1 ring-foreground/10",
        "animate-in duration-150 fade-in-0 slide-in-from-bottom-2",
        className,
      )}
    >
      <span className="px-2 text-xs font-medium text-muted-foreground tabular-nums">
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

"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

export interface SelectionBarProps {
  allChecked: boolean;
  indeterminate?: boolean;
  onAllCheckedChange: (checked: boolean) => void;
  summary: React.ReactNode;
  actions?: React.ReactNode;
  onDeselectAll: () => void;
  /** Hide the action slot (e.g. when a bottom bulk bar renders them instead). */
  hideActions?: boolean;
  className?: string;
}

export function SelectionBar({
  allChecked,
  indeterminate,
  onAllCheckedChange,
  summary,
  actions,
  onDeselectAll,
  hideActions,
  className,
}: SelectionBarProps) {
  const handleCheckedChange = React.useCallback(
    (c: boolean | "indeterminate") => onAllCheckedChange(c === true),
    [onAllCheckedChange],
  );
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 sm:gap-3 sm:px-4 sm:py-2.5",
        className,
      )}
    >
      <label className="flex min-h-9 cursor-pointer items-center gap-2 coarse:min-h-11">
        <Checkbox
          checked={allChecked}
          indeterminate={indeterminate}
          onCheckedChange={handleCheckedChange}
          aria-label="Select all"
        />
        <span className="text-sm text-muted-foreground">{summary}</span>
      </label>
      <div className="ml-auto flex flex-wrap items-center gap-2 max-sm:basis-full max-sm:[&>button]:flex-1">
        {hideActions ? null : actions}
        <Button size="sm" variant="ghost" onClick={onDeselectAll}>
          Deselect All
        </Button>
      </div>
    </div>
  );
}

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
  className?: string;
}

export function SelectionBar({
  allChecked,
  indeterminate,
  onAllCheckedChange,
  summary,
  actions,
  onDeselectAll,
  className,
}: SelectionBarProps) {
  const handleCheckedChange = React.useCallback(
    (c: boolean | "indeterminate") => onAllCheckedChange(c === true),
    [onAllCheckedChange],
  );
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-lg border bg-muted/40 px-4 py-2.5",
        className,
      )}
    >
      <Checkbox
        checked={allChecked}
        indeterminate={indeterminate}
        onCheckedChange={handleCheckedChange}
        aria-label="Select all"
      />
      <span className="text-sm text-muted-foreground">{summary}</span>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {actions}
        <Button size="sm" variant="ghost" onClick={onDeselectAll}>
          Deselect All
        </Button>
      </div>
    </div>
  );
}

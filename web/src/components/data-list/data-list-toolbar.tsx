"use client";

import * as React from "react";
import { ArrowUpDownIcon, LayersIcon, SlidersHorizontalIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { SortOption } from "./types";

export interface DataListToolbarProps<T> {
  /** Unified search/filter field (full element passed in by orchestrator). */
  searchFilter: React.ReactNode;
  sortOptions?: SortOption<T>[];
  sort?: string;
  onSortChange?: (id: string) => void;
  groupOptions?: { id: string; label: string }[];
  group?: string;
  onGroupChange?: (id: string) => void;
  /** Optional left-side slot (rare; breadcrumb usually carries the title). */
  leading?: React.ReactNode;
  /** Page-level actions (Invite, Add, etc). Separated by divider on the right. */
  trailing?: React.ReactNode;
  className?: string;
}

export function DataListToolbar<T>({
  searchFilter,
  sortOptions,
  sort,
  onSortChange,
  groupOptions,
  group,
  onGroupChange,
  leading,
  trailing,
  className,
}: DataListToolbarProps<T>) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {leading}

      <div className="flex min-w-[260px] flex-1 items-center">{searchFilter}</div>

      {sortOptions && sortOptions.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="outline" size="default" className="gap-1 text-xs">
                <ArrowUpDownIcon className="h-4 w-4" />
                {sortOptions.find((o) => o.id === sort)?.label ?? "Sort"}
              </Button>
            }
          />
          <DropdownMenuContent align="end">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Sort by</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={sort} onValueChange={onSortChange}>
                {sortOptions.map((o) => (
                  <DropdownMenuRadioItem key={o.id} value={o.id}>
                    {o.label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {groupOptions && groupOptions.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="outline" size="default" className="gap-1 text-xs">
                <LayersIcon className="h-4 w-4" />
                {group && group !== "none"
                  ? (groupOptions.find((g) => g.id === group)?.label ?? "Group")
                  : "None"}
              </Button>
            }
          />
          <DropdownMenuContent align="end">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Group by</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={group ?? "none"} onValueChange={onGroupChange}>
                <DropdownMenuRadioItem value="none">None</DropdownMenuRadioItem>
                <DropdownMenuSeparator />
                {groupOptions.map((g) => (
                  <DropdownMenuRadioItem key={g.id} value={g.id}>
                    {g.label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {trailing && (
        <>
          <span className="hidden h-6 w-px bg-border sm:block" />
          <div className="flex items-center gap-2">{trailing}</div>
        </>
      )}
    </div>
  );
}

/** Generic display-options dropdown for advanced settings. */
export function DataListDisplayMenu({ children }: { children: React.ReactNode }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="outline" size="default" className="gap-1 text-xs">
            <SlidersHorizontalIcon className="h-4 w-4" />
            Display
          </Button>
        }
      />
      <DropdownMenuContent align="end" className="w-56">
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export { DropdownMenuCheckboxItem };

"use client";

import * as React from "react";
import { ArrowUpDownIcon, CheckIcon, LayersIcon, SlidersHorizontalIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
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
import { useIsMobile } from "@/hooks/use-mobile";
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

/** Search slot: full first row on phones, inline min-width on sm+. */
export const TOOLBAR_SEARCH_SLOT_CLASS =
  "flex min-w-0 basis-full items-center sm:min-w-[260px] sm:flex-1 sm:basis-auto";

interface OptionListProps {
  title: string;
  options: { id: string; label: string }[];
  value?: string;
  onChange?: (id: string) => void;
}

/** Touch-friendly radio list used inside the mobile Filters drawer. */
function DrawerOptionList({ title, options, value, onChange }: OptionListProps) {
  return (
    <div role="radiogroup" aria-label={title} className="flex flex-col gap-1">
      <div className="px-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </div>
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange?.(o.id)}
            className={cn(
              "flex min-h-11 items-center justify-between rounded-md px-3 text-left text-sm",
              active ? "bg-accent text-accent-foreground" : "hover:bg-muted",
            )}
          >
            {o.label}
            {active && <CheckIcon className="h-4 w-4" />}
          </button>
        );
      })}
    </div>
  );
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
  const isMobile = useIsMobile();
  const hasSort = !!sortOptions && sortOptions.length > 0;
  const hasGroup = !!groupOptions && groupOptions.length > 0;
  const groupValue = group ?? "none";
  const activeCount = (hasSort && sort ? 1 : 0) + (hasGroup && groupValue !== "none" ? 1 : 0);

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {leading}

      <div className={TOOLBAR_SEARCH_SLOT_CLASS}>{searchFilter}</div>

      {isMobile && (hasSort || hasGroup) ? (
        <Drawer>
          <DrawerTrigger asChild>
            <Button variant="outline" size="default" className="gap-1 text-xs">
              <SlidersHorizontalIcon className="h-4 w-4" />
              Filters
              {activeCount > 0 && (
                <span className="rounded-full bg-primary px-1.5 text-[10px] leading-4 text-primary-foreground tabular-nums">
                  {activeCount}
                </span>
              )}
            </Button>
          </DrawerTrigger>
          <DrawerContent className="pb-safe-b">
            <DrawerHeader className="text-left">
              <DrawerTitle>Sort &amp; group</DrawerTitle>
              <DrawerDescription className="sr-only">
                Choose sort order and grouping
              </DrawerDescription>
            </DrawerHeader>
            <div className="flex flex-col gap-5 overflow-y-auto px-4 pb-4">
              {hasSort && sortOptions && (
                <DrawerOptionList
                  title="Sort by"
                  options={sortOptions.map((o) => ({ id: o.id, label: o.label }))}
                  value={sort}
                  onChange={onSortChange}
                />
              )}
              {hasGroup && groupOptions && (
                <DrawerOptionList
                  title="Group by"
                  options={[{ id: "none", label: "None" }, ...groupOptions]}
                  value={groupValue}
                  onChange={onGroupChange}
                />
              )}
            </div>
          </DrawerContent>
        </Drawer>
      ) : (
        <>
          {hasSort && sortOptions && (
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

          {hasGroup && groupOptions && (
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button variant="outline" size="default" className="gap-1 text-xs">
                    <LayersIcon className="h-4 w-4" />
                    {groupValue !== "none"
                      ? (groupOptions.find((g) => g.id === groupValue)?.label ?? "Group")
                      : "None"}
                  </Button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Group by</DropdownMenuLabel>
                  <DropdownMenuRadioGroup value={groupValue} onValueChange={onGroupChange}>
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
        </>
      )}

      {trailing && (
        <>
          <span className="hidden h-6 w-px bg-border sm:block" />
          <div className="ml-auto flex items-center gap-2 sm:ml-0">{trailing}</div>
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

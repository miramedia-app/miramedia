"use client";

import * as React from "react";
import type { DateRange } from "react-day-picker";
import { ArrowUpDownIcon, CalendarRange } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { DataListSearchFilter } from "@/components/data-list/data-list-search-filter";
import type { ActiveFilter } from "@/components/data-list/types";
import {
  formatUpcomingDateHeading,
  parseIsoDate,
  toIsoDate,
  upcomingFacets,
  type UpcomingItem,
  type UpcomingSort,
} from "@/lib/upcoming";

export type UpcomingWindow = { start: string; end: string };

export const sortOptions: { value: UpcomingSort; label: string }[] = [
  { value: "date-asc", label: "Soonest first" },
  { value: "date-desc", label: "Latest first" },
];

/** Presets are resolved against `today` at click time, never memoized. */
export const windowPresets: { id: string; label: string; pastDays: number; futureDays: number }[] =
  [
    { id: "next-7", label: "Next 7 days", pastDays: 0, futureDays: 7 },
    { id: "next-30", label: "Next 30 days", pastDays: 0, futureDays: 30 },
    { id: "next-90", label: "Next 3 months", pastDays: 0, futureDays: 90 },
    { id: "past-week-next-month", label: "Past week + next month", pastDays: 7, futureDays: 28 },
    { id: "past-30", label: "Past 30 days", pastDays: 30, futureDays: 0 },
  ];

export const DEFAULT_PRESET_ID = "next-30";

function shiftDays(from: Date, days: number): Date {
  const next = new Date(from);
  next.setDate(next.getDate() + days);
  return next;
}

export function presetWindow(pastDays: number, futureDays: number): UpcomingWindow {
  const today = new Date();
  return {
    start: toIsoDate(shiftDays(today, -pastDays)),
    end: toIsoDate(shiftDays(today, futureDays)),
  };
}

export function defaultWindow(pastDays = 0, futureDays = 30): UpcomingWindow {
  return presetWindow(pastDays, futureDays);
}

/**
 * Effective upcoming window: the user's explicit pick always wins; otherwise
 * the server-configured default once features have resolved; null while the
 * features query is still pending (callers gate their fetch on non-null).
 */
export function resolveUpcomingWindow({
  override,
  featuresReady,
  pastDays,
  futureDays,
}: {
  override: UpcomingWindow | null;
  featuresReady: boolean;
  pastDays: number;
  futureDays: number;
}): UpcomingWindow | null {
  if (override) return override;
  if (!featuresReady) return null;
  return defaultWindow(pastDays, futureDays);
}

export type RangeStep = {
  /** Range to show while picking; undefined falls back to the committed window. */
  draft: DateRange | undefined;
  /** Set once both ends are known — the caller commits and closes the popover. */
  commit: UpcomingWindow | null;
};

/**
 * One click of the two-click range interaction.
 *
 * `current` is what the calendar is showing. When that is already a complete
 * range, react-day-picker reports the click as an *extension* of it (it moves
 * `to`), but the window is committed at that point — the user means "start
 * over". So the click is reinterpreted as a fresh `from`, and only the click
 * that closes an open range commits.
 */
export function nextRangeStep(
  current: DateRange | undefined,
  next: DateRange | undefined,
  triggerDate: Date,
): RangeStep {
  if (current?.from && current.to) {
    return { draft: { from: triggerDate, to: undefined }, commit: null };
  }
  if (next?.from && next.to) {
    return {
      draft: undefined,
      commit: { start: toIsoDate(next.from), end: toIsoDate(next.to) },
    };
  }
  return { draft: next, commit: null };
}

export function UpcomingControls({
  window,
  onWindowChange,
  sort,
  onSortChange,
  search,
  onSearchChange,
  filters,
  onFiltersChange,
}: {
  window: UpcomingWindow;
  onWindowChange: (next: UpcomingWindow) => void;
  sort: UpcomingSort;
  onSortChange: (next: UpcomingSort) => void;
  search: string;
  onSearchChange: (next: string) => void;
  filters: ActiveFilter[];
  onFiltersChange: (next: ActiveFilter[]) => void;
}) {
  const [open, setOpen] = React.useState(false);
  // Draft range so a half-finished selection (from picked, to not yet) never
  // fires a refetch with a collapsed window.
  const [draft, setDraft] = React.useState<DateRange | undefined>(undefined);

  const selected: DateRange | undefined = React.useMemo(() => {
    const from = parseIsoDate(window.start) ?? undefined;
    const to = parseIsoDate(window.end) ?? undefined;
    return from ? { from, to } : undefined;
  }, [window.start, window.end]);

  const range = draft ?? selected;
  const label = `${formatUpcomingDateHeading(window.start)} – ${formatUpcomingDateHeading(window.end)}`;

  function handleSelect(next: DateRange | undefined, triggerDate: Date) {
    const step = nextRangeStep(range, next, triggerDate);
    setDraft(step.draft);
    if (step.commit) {
      onWindowChange(step.commit);
      setOpen(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <DataListSearchFilter<UpcomingItem>
        search={search}
        onSearchChange={onSearchChange}
        facets={upcomingFacets}
        filters={filters}
        onFiltersChange={onFiltersChange}
        placeholder="Search or filter upcoming…"
        className="w-full sm:w-72"
      />
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setDraft(undefined);
        }}
      >
        <PopoverTrigger
          render={
            <Button variant="outline" size="default" className="gap-1 text-xs">
              <CalendarRange className="h-4 w-4" />
              {label}
            </Button>
          }
        />
        <PopoverContent align="start" className="w-auto p-2">
          <div className="flex flex-wrap gap-1">
            {windowPresets.map((preset) => (
              <Button
                key={preset.id}
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => {
                  setDraft(undefined);
                  onWindowChange(presetWindow(preset.pastDays, preset.futureDays));
                  setOpen(false);
                }}
              >
                {preset.label}
              </Button>
            ))}
          </div>
          <Separator />
          <Calendar
            mode="range"
            numberOfMonths={2}
            defaultMonth={range?.from}
            selected={range}
            onSelect={handleSelect}
            captionLayout="dropdown"
            // Two months side by side: leading/trailing days would render the
            // same date in both grids, and range highlighting paints both
            // copies — so the selected end appears twice.
            showOutsideDays={false}
            autoFocus
          />
        </PopoverContent>
      </Popover>

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="outline" size="default" className="gap-1 text-xs">
              <ArrowUpDownIcon className="h-4 w-4" />
              {sortOptions.find((o) => o.value === sort)?.label ?? "Sort"}
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Sort by</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={sort}
              onValueChange={(value) => onSortChange(value as UpcomingSort)}
            >
              {sortOptions.map((o) => (
                <DropdownMenuRadioItem key={o.value} value={o.value}>
                  {o.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

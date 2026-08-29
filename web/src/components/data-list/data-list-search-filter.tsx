"use client";
// Unified search + filter combobox.

import * as React from "react";
import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { CheckIcon, ChevronLeftIcon, SearchIcon, XIcon } from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { cn } from "@/lib/utils";
import type { ActiveFilter, FacetDef, FilterOperator } from "./types";

export interface DataListSearchFilterProps<T> {
  search: string;
  onSearchChange: (next: string) => void;
  facets?: FacetDef<T>[];
  filters: ActiveFilter[];
  onFiltersChange: (next: ActiveFilter[]) => void;
  placeholder?: string;
  inputRef?: React.RefObject<HTMLInputElement | null>;
  className?: string;
}

const OPERATOR_LABEL: Record<FilterOperator, string> = {
  is: "is",
  is_not: "is not",
  includes: "is",
  excludes: "is not",
};

export function DataListSearchFilter<T>({
  search,
  onSearchChange,
  facets,
  filters,
  onFiltersChange,
  placeholder = "Search or filter…",
  inputRef,
  className,
}: DataListSearchFilterProps<T>) {
  const [open, setOpen] = React.useState(false);
  const [editingFacetId, setEditingFacetId] = React.useState<string | null>(null);
  const innerRef = React.useRef<HTMLInputElement>(null);
  const ref = inputRef ?? innerRef;
  const containerRef = React.useRef<HTMLDivElement>(null);

  function upsertFilter(facetId: string, operator: FilterOperator, values: string[]) {
    if (values.length === 0) {
      onFiltersChange(filters.filter((f) => f.facetId !== facetId));
      return;
    }
    const idx = filters.findIndex((f) => f.facetId === facetId);
    if (idx === -1) onFiltersChange([...filters, { facetId, operator, values }]);
    else {
      const next = [...filters];
      next[idx] = { facetId, operator, values };
      onFiltersChange(next);
    }
  }

  function removeFilter(facetId: string) {
    onFiltersChange(filters.filter((f) => f.facetId !== facetId));
  }

  function toggleOperator(facetId: string) {
    const idx = filters.findIndex((f) => f.facetId === facetId);
    if (idx === -1) return;
    const f = filters[idx];
    const next = [...filters];
    next[idx] = {
      ...f,
      operator:
        f.operator === "includes"
          ? "excludes"
          : f.operator === "excludes"
            ? "includes"
            : f.operator === "is"
              ? "is_not"
              : "is",
    };
    onFiltersChange(next);
  }

  function openPopover() {
    if (!open) setOpen(true);
  }

  function handleContainerMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    // Only intercept clicks on the bare container (gaps); chip/button clicks bubble naturally.
    if (e.target === e.currentTarget) {
      e.preventDefault();
      ref.current?.focus();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setOpen(false);
      e.currentTarget.blur();
      return;
    }
    if (e.key === "Backspace" && search === "" && filters.length > 0) {
      e.preventDefault();
      const last = filters[filters.length - 1];
      removeFilter(last.facetId);
      return;
    }
    if (e.key === "ArrowDown" && !open) {
      setOpen(true);
    }
  }

  // Facet lookup map + per-facet option→label map. Built once per facets
  // change so chip rendering is O(filters) instead of O(filters × facets ×
  // options).
  const facetMaps = React.useMemo(() => {
    const byId = new Map<string, FacetDef<T>>();
    const optionLabelByFacet = new Map<string, Map<string, string>>();
    for (const f of facets ?? []) {
      byId.set(f.id, f);
      const labels = new Map<string, string>();
      for (const o of f.options) labels.set(o.value, o.label);
      optionLabelByFacet.set(f.id, labels);
    }
    return { byId, optionLabelByFacet };
  }, [facets]);

  const editingFacet = editingFacetId ? (facetMaps.byId.get(editingFacetId) ?? null) : null;

  return (
    <PopoverPrimitive.Root
      open={open}
      onOpenChange={(o, details) => {
        // Ignore close events triggered by clicking the field itself —
        // we want the popover to stay open while interacting with chips/input.
        if (!o && details.reason === "trigger-press") return;
        setOpen(o);
        if (!o) setEditingFacetId(null);
      }}
    >
      <PopoverPrimitive.Trigger
        nativeButton={false}
        render={
          <div
            ref={containerRef}
            className={cn(
              "flex min-h-8 min-w-0 flex-1 flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-sm shadow-xs transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50 coarse:min-h-11",
              className,
            )}
            onMouseDown={handleContainerMouseDown}
            onClick={(e) => {
              if (e.target === e.currentTarget) ref.current?.focus();
            }}
          />
        }
      >
        <SearchIcon
          className="h-3.5 w-3.5 shrink-0 cursor-text text-muted-foreground"
          onMouseDown={(e) => {
            e.preventDefault();
            ref.current?.focus();
          }}
        />
        {filters.map((f) => {
          const facet = facetMaps.byId.get(f.facetId);
          if (!facet) return null;
          const optionLabels = facetMaps.optionLabelByFacet.get(f.facetId);
          const labels = f.values.slice(0, 3).map((v) => optionLabels?.get(v) ?? v);
          const overflow = f.values.length - labels.length;
          return (
            <span
              key={f.facetId}
              className="inline-flex h-6 items-center gap-1 rounded bg-muted pr-0.5 pl-1.5 text-xs"
              onMouseDown={(e) => e.stopPropagation()}
            >
              <span className="font-medium">{facet.label}</span>
              <button
                type="button"
                className="rounded px-1 text-muted-foreground hover:bg-background hover:text-foreground"
                onClick={(e) => {
                  e.stopPropagation();
                  toggleOperator(f.facetId);
                }}
              >
                {OPERATOR_LABEL[f.operator]}
              </button>
              <button
                type="button"
                className="rounded px-1 hover:bg-background"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingFacetId(f.facetId);
                  setOpen(true);
                }}
              >
                {labels.join(", ")}
                {overflow > 0 && <span className="text-muted-foreground"> +{overflow}</span>}
              </button>
              <button
                type="button"
                className="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                onClick={(e) => {
                  e.stopPropagation();
                  removeFilter(f.facetId);
                }}
                aria-label={`Remove ${facet.label} filter`}
              >
                <XIcon className="h-3 w-3" />
              </button>
            </span>
          );
        })}
        <input
          ref={ref}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          onFocus={openPopover}
          onMouseDown={(e) => {
            // Don't let the container intercept; just open + focus naturally.
            e.stopPropagation();
            openPopover();
          }}
          onKeyDown={handleKeyDown}
          placeholder={filters.length === 0 ? placeholder : ""}
          className="min-w-[80px] flex-1 bg-transparent text-[16px] outline-none placeholder:text-muted-foreground sm:text-sm"
        />
        {(search.length > 0 || filters.length > 0) && (
          <button
            type="button"
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              onSearchChange("");
              onFiltersChange([]);
              ref.current?.focus();
            }}
            aria-label="Clear"
          >
            <XIcon className="h-3.5 w-3.5" />
          </button>
        )}
      </PopoverPrimitive.Trigger>

      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner
          side="bottom"
          align="start"
          sideOffset={6}
          className="isolate z-50"
        >
          <PopoverPrimitive.Popup
            initialFocus={false}
            finalFocus={false}
            className="z-50 w-[min(var(--anchor-width),calc(100vw-2rem))] min-w-[min(280px,calc(100vw-2rem))] origin-(--transform-origin) rounded-lg bg-popover p-0 text-popover-foreground shadow-md ring-1 ring-foreground/10 outline-hidden duration-100 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          >
            <Command shouldFilter={!editingFacet}>
              {editingFacet ? (
                <FacetValuePicker
                  key={editingFacet.id}
                  facet={editingFacet}
                  active={filters.find((f) => f.facetId === editingFacet.id)?.values ?? []}
                  onBack={() => setEditingFacetId(null)}
                  onApply={(values) => {
                    const op = editingFacet.defaultOperator ?? "includes";
                    upsertFilter(editingFacet.id, op, values);
                    setEditingFacetId(null);
                  }}
                />
              ) : (
                <FacetSuggestions
                  facets={facets ?? []}
                  filters={filters}
                  query={search}
                  onPickFacet={(facetId) => setEditingFacetId(facetId)}
                  onPickValue={(facetId, value) => {
                    const facet = facets?.find((f) => f.id === facetId);
                    if (!facet) return;
                    const op = facet.defaultOperator ?? "includes";
                    const existing = filters.find((f) => f.facetId === facetId);
                    const next = existing
                      ? Array.from(new Set([...existing.values, value]))
                      : [value];
                    upsertFilter(facetId, op, next);
                    onSearchChange("");
                  }}
                />
              )}
            </Command>
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

function FacetSuggestions<T>({
  facets,
  filters,
  query,
  onPickFacet,
  onPickValue,
}: {
  facets: FacetDef<T>[];
  filters: ActiveFilter[];
  query: string;
  onPickFacet: (facetId: string) => void;
  onPickValue: (facetId: string, value: string) => void;
}) {
  const q = query.trim().toLowerCase();
  const hasQuery = q.length > 0;

  const matchedFacets = !hasQuery
    ? facets
    : facets.filter((f) => f.label.toLowerCase().includes(q));

  const matchedValues = !hasQuery
    ? []
    : facets.flatMap((f) =>
        f.options
          .filter(
            (o) =>
              o.label.toLowerCase().includes(q) ||
              (o.keywords ?? []).some((k) => k.toLowerCase().includes(q)),
          )
          .map((o) => ({ facet: f, option: o })),
      );

  return (
    <CommandList className="max-h-80">
      {hasQuery ? (
        <CommandEmpty>No filter match. Press Enter to search by text.</CommandEmpty>
      ) : (
        <CommandEmpty>No filters available</CommandEmpty>
      )}

      {matchedFacets.length > 0 && (
        <CommandGroup heading="Filter by">
          {matchedFacets.map((facet) => {
            const active = filters.some((f) => f.facetId === facet.id);
            return (
              <CommandItem
                key={facet.id}
                value={`facet-${facet.id} ${facet.label}`}
                onSelect={() => onPickFacet(facet.id)}
              >
                {facet.icon}
                <span>{facet.label}</span>
                {active && <CheckIcon className="ml-auto h-3.5 w-3.5 text-muted-foreground" />}
              </CommandItem>
            );
          })}
        </CommandGroup>
      )}

      {matchedValues.length > 0 && (
        <>
          <CommandSeparator />
          <CommandGroup heading="Values">
            {matchedValues.slice(0, 8).map(({ facet, option }) => (
              <CommandItem
                key={`${facet.id}:${option.value}`}
                value={`value-${facet.id}-${option.value} ${option.label} ${(option.keywords ?? []).join(" ")}`}
                onSelect={() => onPickValue(facet.id, option.value)}
              >
                {option.icon}
                <span className="truncate text-xs text-muted-foreground">{facet.label}</span>
                <span className="truncate">{option.label}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        </>
      )}
    </CommandList>
  );
}

function FacetValuePicker<T>({
  facet,
  active,
  onBack,
  onApply,
}: {
  facet: FacetDef<T>;
  active: string[];
  onBack: () => void;
  onApply: (values: string[]) => void;
}) {
  const [staged, setStaged] = React.useState<string[]>(active);

  function toggle(value: string) {
    setStaged((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    );
  }

  return (
    <>
      <div className="flex items-center gap-1 border-b px-1 py-1">
        <button
          type="button"
          className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-xs text-muted-foreground hover:bg-muted"
          onClick={onBack}
        >
          <ChevronLeftIcon className="h-3.5 w-3.5" />
          {facet.label}
        </button>
        <button
          type="button"
          className="ml-auto inline-flex h-6 items-center rounded px-2 text-xs font-medium text-primary hover:bg-primary/10"
          onClick={() => onApply(staged)}
        >
          Apply
        </button>
      </div>
      <CommandInput placeholder={`Filter ${facet.label.toLowerCase()}…`} autoFocus />
      <CommandList>
        <CommandEmpty>No matches</CommandEmpty>
        <CommandGroup>
          {facet.options.map((opt) => {
            const checked = staged.includes(opt.value);
            return (
              <CommandItem
                key={opt.value}
                value={`${opt.label} ${(opt.keywords ?? []).join(" ")}`}
                onSelect={() => toggle(opt.value)}
              >
                <span
                  className={cn(
                    "flex h-3.5 w-3.5 items-center justify-center rounded-[3px] border",
                    checked ? "border-primary bg-primary text-primary-foreground" : "border-input",
                  )}
                >
                  {checked && <CheckIcon className="h-2.5 w-2.5" />}
                </span>
                {opt.icon}
                <span className="truncate">{opt.label}</span>
                {opt.count != null && (
                  <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                    {opt.count}
                  </span>
                )}
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </>
  );
}

"use client";

import * as React from "react";
import { Search, LoaderCircle, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetchMediaSuggestions } from "@/lib/media-search";
import type { MediaSearchResult as SearchResult } from "@/lib/media-search";

// Stable empty-array fallback so dependent effects don't see fresh array
// identity per render while the query is loading.
const EMPTY_SUGGESTIONS: SearchResult[] = [];

export function MediaSearchInput({
  value,
  onValueChange,
  mediaType = "show",
  placeholder = "Search...",
  onSelect,
  onSubmit,
  onSubmitClick,
  submitLoading = false,
}: {
  value: string;
  onValueChange: (v: string) => void;
  mediaType?: "show" | "movie";
  placeholder?: string;
  onSelect?: (result: SearchResult) => void;
  onSubmit?: (query: string) => void;
  onSubmitClick?: () => void;
  submitLoading?: boolean;
}) {
  const [showDropdown, setShowDropdown] = React.useState(false);
  const [selectedIndex, setSelectedIndex] = React.useState(-1);
  const [debouncedQuery, setDebouncedQuery] = React.useState("");
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const blurTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const hasSubmitButton = !!onSubmitClick;

  // React Query handles dedup, caching, and request cancellation on unmount
  // — so the prior pattern of fire-and-forget promises (which could leak
  // setState calls after unmount) is no longer needed.
  const suggestionsQuery = useQuery({
    queryKey: ["media-search", mediaType, debouncedQuery],
    queryFn: ({ signal }) => fetchMediaSuggestions(mediaType, debouncedQuery, signal),
    enabled: debouncedQuery.length >= 2,
    staleTime: 60 * 1000,
  });

  const suggestions = suggestionsQuery.data ?? EMPTY_SUGGESTIONS;
  // A provider outage must not read as "no results" — the dropdown says so.
  const searchFailed = suggestionsQuery.isError;

  // Open the dropdown once results arrive — only when the user is still in
  // a "searching" state (focused input with chars). A failed search opens it
  // too, so the failure is visible.
  React.useEffect(() => {
    if (debouncedQuery.length >= 2 && (suggestions.length > 0 || searchFailed)) {
      setShowDropdown(true);
      setSelectedIndex(-1);
    }
  }, [debouncedQuery, suggestions, searchFailed]);

  function handleChange(next: string) {
    onValueChange(next);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (next.length < 2) {
      setDebouncedQuery("");
      setShowDropdown(false);
      return;
    }
    debounceRef.current = setTimeout(() => setDebouncedQuery(next), 300);
  }

  function selectSuggestion(result: SearchResult) {
    onValueChange(result.name + (result.year != null ? ` (${result.year})` : ""));
    setShowDropdown(false);
    onSelect?.(result);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && showDropdown) {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp" && showDropdown) {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (selectedIndex >= 0 && selectedIndex < suggestions.length) {
        selectSuggestion(suggestions[selectedIndex]!);
      } else {
        setShowDropdown(false);
        onSubmit?.(value);
      }
    } else if (e.key === "Escape") {
      setShowDropdown(false);
      setSelectedIndex(-1);
    }
  }

  function clear() {
    onValueChange("");
    setDebouncedQuery("");
    setShowDropdown(false);
    inputRef.current?.focus();
  }

  // Clean up any pending timers on unmount so they can't fire setState on a
  // dead component.
  React.useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (blurTimeoutRef.current) clearTimeout(blurTimeoutRef.current);
    },
    [],
  );

  return (
    <div className="relative w-full">
      <div className="flex items-center gap-2">
        <div className="relative flex h-8 min-w-[260px] flex-1 items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-sm shadow-xs transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => handleChange(e.target.value)}
            placeholder={placeholder}
            type="text"
            className="min-w-[80px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            onKeyDown={handleKeyDown}
            onFocus={() => {
              if ((suggestions.length > 0 || searchFailed) && value.length >= 2)
                setShowDropdown(true);
            }}
            onBlur={() => {
              // Use a ref so the timer can be cancelled on unmount.
              if (blurTimeoutRef.current) clearTimeout(blurTimeoutRef.current);
              blurTimeoutRef.current = setTimeout(() => setShowDropdown(false), 200);
            }}
            autoComplete="off"
          />
          {value.length > 0 ? (
            <button
              type="button"
              className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={clear}
              aria-label="Clear"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
        {hasSubmitButton && (
          <button
            type="button"
            className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
            onClick={onSubmitClick}
            disabled={submitLoading}
          >
            {submitLoading ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="h-3.5 w-3.5" />
            )}
            Search
          </button>
        )}
      </div>

      {showDropdown && searchFailed && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-lg border bg-popover shadow-lg">
          <div className="px-3 py-2 text-left text-sm text-muted-foreground">
            Search unavailable
          </div>
        </div>
      )}

      {showDropdown && !searchFailed && suggestions.length > 0 && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-lg border bg-popover shadow-lg">
          {suggestions.map((suggestion, i) => (
            <button
              key={String(suggestion.external_id)}
              type="button"
              className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors hover:bg-accent ${
                i === selectedIndex ? "bg-accent" : ""
              }`}
              onMouseDown={() => selectSuggestion(suggestion)}
            >
              {suggestion.poster_path ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={suggestion.poster_path}
                  alt=""
                  className="h-12 w-8 shrink-0 rounded object-cover"
                  loading="lazy"
                  decoding="async"
                  width={32}
                  height={48}
                />
              ) : (
                <div className="flex h-12 w-8 shrink-0 items-center justify-center rounded bg-muted text-muted-foreground">
                  <Search className="h-3 w-3" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="truncate font-medium">{suggestion.name}</span>
                  {suggestion.year != null && (
                    <span className="shrink-0 text-xs text-muted-foreground">
                      ({suggestion.year})
                    </span>
                  )}
                </div>
                {suggestion.overview && (
                  <p className="truncate text-xs text-muted-foreground">{suggestion.overview}</p>
                )}
              </div>
              {suggestion.added && (
                <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                  Added
                </span>
              )}
              {suggestion.vote_average != null && (
                <span className="shrink-0 text-xs text-muted-foreground">
                  ★ {Math.round(suggestion.vote_average)}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

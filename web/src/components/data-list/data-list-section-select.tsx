"use client";

import * as React from "react";
import { CheckIcon, ListChecksIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";

/**
 * Mobile select-mode state for a `DataListSection` that lives outside a
 * `DataList` (detail pages). Mirrors `DataList`'s own toggle: on phones the
 * checkbox column is hidden until the user taps the toggle, and leaving
 * select mode clears the selection. On desktop `selectMode` is always true
 * so the inline `SelectionBar` + always-visible checkboxes are unchanged.
 */
export function useSectionSelectMode(clearSelection: () => void) {
  const isMobile = useIsMobile();
  const [mobileSelectMode, setMobileSelectMode] = React.useState(false);
  const clearRef = React.useRef(clearSelection);
  clearRef.current = clearSelection;
  const toggle = React.useCallback(() => {
    setMobileSelectMode((v) => {
      if (v) clearRef.current();
      return !v;
    });
  }, []);
  return {
    isMobile,
    /** Whether checkboxes should be shown for the section. */
    selectMode: isMobile ? mobileSelectMode : true,
    /** Mobile-only: whether the select toggle is currently "on". */
    mobileSelectMode,
    toggle,
  };
}

export interface DataListSectionSelectToggleProps {
  selectMode: boolean;
  onToggle: () => void;
  className?: string;
}

/** 44px icon button (coarse pointers) that enters / leaves mobile select mode. */
export function DataListSectionSelectToggle({
  selectMode,
  onToggle,
  className,
}: DataListSectionSelectToggleProps) {
  return (
    <Button
      variant={selectMode ? "secondary" : "outline"}
      size="icon"
      aria-pressed={selectMode}
      aria-label={selectMode ? "Done selecting" : "Select rows"}
      data-slot="select-mode-toggle"
      onClick={onToggle}
      className={className}
    >
      {selectMode ? <CheckIcon className="h-4 w-4" /> : <ListChecksIcon className="h-4 w-4" />}
    </Button>
  );
}

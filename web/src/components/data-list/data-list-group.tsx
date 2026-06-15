"use client";

import * as React from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface DataListGroupHeaderProps {
  label: React.ReactNode;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
  className?: string;
}

export function DataListGroupHeader({
  label,
  count,
  collapsed,
  onToggle,
  className,
}: DataListGroupHeaderProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        "sticky top-9 z-[5] flex h-9 w-full items-center gap-2 border-b bg-muted/30 px-4 text-left text-xs font-semibold tracking-wide text-muted-foreground uppercase backdrop-blur transition-colors hover:bg-muted/60",
        className,
      )}
      aria-expanded={!collapsed}
    >
      {collapsed ? (
        <ChevronRightIcon className="h-3.5 w-3.5" />
      ) : (
        <ChevronDownIcon className="h-3.5 w-3.5" />
      )}
      <span className="text-foreground">{label}</span>
      <span className="tabular-nums">{count}</span>
    </button>
  );
}

const STORAGE_PREFIX = "data-list-collapsed:";
const STORAGE_VERSION = 1;

type StoredCollapsed = { v: number; ids: string[] };

export function useCollapsedGroups(storageKey?: string) {
  const [collapsed, setCollapsed] = React.useState<Set<string>>(() => {
    if (typeof window === "undefined" || !storageKey) return new Set();
    try {
      const raw = window.localStorage.getItem(STORAGE_PREFIX + storageKey);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw) as StoredCollapsed;
      if (parsed?.v !== STORAGE_VERSION || !Array.isArray(parsed.ids)) return new Set();
      return new Set(parsed.ids);
    } catch {
      return new Set();
    }
  });

  React.useEffect(() => {
    if (!storageKey || typeof window === "undefined") return;
    const payload: StoredCollapsed = { v: STORAGE_VERSION, ids: Array.from(collapsed) };
    window.localStorage.setItem(STORAGE_PREFIX + storageKey, JSON.stringify(payload));
  }, [collapsed, storageKey]);

  const toggle = React.useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  return { collapsed, toggle };
}

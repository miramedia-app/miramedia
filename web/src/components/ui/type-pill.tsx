import type * as React from "react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Shared pill for a media TYPE or KIND label (Show, Movie, Download, Scan…).
 * Always neutral `outline` so the theme stays monochrome and every page
 * renders type/kind identically. Use {@link StatusPill} for status values.
 */
const outlinePillClass = "h-5 px-1.5 text-[11px]";

export function TypePill({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Badge variant="outline" className={cn(outlinePillClass, className)}>
      {children}
    </Badge>
  );
}

/**
 * Outline pill for neutral table metadata (quality, S/E, language, counts).
 * Same visual as {@link TypePill}; use TypePill for type/role/kind labels only.
 */
export function MetaPill({
  children,
  className,
  ...props
}: {
  children: ReactNode;
  className?: string;
} & React.ComponentProps<typeof Badge>) {
  return (
    <Badge variant="outline" className={cn(outlinePillClass, className)} {...props}>
      {children}
    </Badge>
  );
}

"use client";

import * as React from "react";
import type { DataListDensity } from "./types";
import { cn } from "@/lib/utils";

export interface DataListSkeletonProps {
  rows?: number;
  density?: DataListDensity;
  className?: string;
}

export function DataListSkeleton({
  rows = 10,
  density = "standard",
  className,
}: DataListSkeletonProps) {
  const h = density === "rich" ? "h-14" : density === "compact" ? "h-9" : "h-11";
  return (
    <div className={cn("flex flex-col", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={cn("flex items-center gap-3 border-b border-border/40 px-4", h)}>
          <div className="h-3.5 w-3.5 shrink-0 rounded-sm bg-muted/60" />
          <div className="h-3 w-12 shrink-0 rounded bg-muted/60" />
          <div className="h-3 flex-1 animate-pulse rounded bg-muted/40" />
          <div className="h-3 w-16 shrink-0 rounded bg-muted/60" />
          <div className="h-3 w-20 shrink-0 rounded bg-muted/60" />
        </div>
      ))}
    </div>
  );
}

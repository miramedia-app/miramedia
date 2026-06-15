"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface DataListEmptyProps {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

export function DataListEmpty({ icon, title, description, action, className }: DataListEmptyProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-16 text-center",
        className,
      )}
    >
      {icon && (
        // Fixed icon slot. The descendant rule forces every icon (whatever
        // size class the caller passed) to a uniform 24px, so the empty state
        // looks identical across pages regardless of which lucide icon is used.
        <div className="mb-4 flex size-12 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground [&_svg]:size-6 [&_svg]:shrink-0">
          {icon}
        </div>
      )}
      <div className="text-sm font-medium text-foreground">{title}</div>
      {description && (
        <div className="mt-1.5 max-w-xs text-xs leading-relaxed text-balance text-muted-foreground">
          {description}
        </div>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

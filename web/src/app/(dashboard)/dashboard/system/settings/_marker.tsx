"use client";

import * as React from "react";
import { RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { OverrideCtx, formatDefault, getAt } from "./_shared";

export function OverrideMarker({ path }: { path: string[] }) {
  const ctx = React.useContext(OverrideCtx);
  if (!ctx) return null;
  if (path.length === 0 || !ctx.isOverridden(path[0]!, ...path.slice(1))) return null;
  return (
    <span className="ml-1 inline-flex items-center gap-1 align-middle">
      <Badge
        variant="outline"
        className="text-xs"
        title={`Default: ${formatDefault(getAt(ctx.defaults, path))}`}
      >
        overridden
      </Badge>
      <button
        type="button"
        className="text-muted-foreground hover:text-foreground"
        title={`Reset ${path.join(".")} to default`}
        aria-label={`Reset ${path.join(".")} to default`}
        onClick={() => void ctx.resetField(path)}
      >
        <RotateCcw className="h-3 w-3" />
      </button>
    </span>
  );
}

"use client";

import { Info } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { components } from "@/lib/api/api";
import { cn } from "@/lib/utils";

type Breakdown = components["schemas"]["MatchBreakdown"];

export function MatchConfidencePill({
  confidence,
  breakdown,
}: {
  confidence: number | null | undefined;
  breakdown?: Breakdown | null;
}) {
  const c = confidence ?? 0;
  const pct = Math.round(c * 100);
  const colorClass = c >= 0.8 ? "text-green-600" : c >= 0.5 ? "text-yellow-600" : "text-red-600";

  const pill = (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium", colorClass)}>
      {pct}%{breakdown && <Info size={12} className="opacity-50" />}
    </span>
  );

  if (!breakdown) return pill;

  // Single TooltipProvider is mounted at app/layout.tsx — adding one per
  // pill produced N providers when N pills rendered (e.g. 200 in imports
  // page).
  return (
    <Tooltip>
      <TooltipTrigger render={<span />}>{pill}</TooltipTrigger>
      <TooltipContent className="max-w-[280px] text-xs">
        <div className="font-semibold">Why this match?</div>
        <div className="mt-1 grid grid-cols-2 gap-x-2 gap-y-0.5">
          <span className="text-muted-foreground">Overlap:</span>
          <span className="font-mono">
            {breakdown.overlap_words.length}/{breakdown.media_word_count} words
          </span>
          <span className="text-muted-foreground">Words:</span>
          <span className="font-mono">{breakdown.overlap_words.join(", ") || "—"}</span>
          <span className="text-muted-foreground">Base score:</span>
          <span className="font-mono">{breakdown.base_score.toFixed(2)}</span>
          <span className="text-muted-foreground">Year boost:</span>
          <span className="font-mono">+{breakdown.year_boost.toFixed(2)}</span>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

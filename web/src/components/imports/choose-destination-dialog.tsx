"use client";

import * as React from "react";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MatchConfidencePill } from "@/components/match-confidence-pill";
import { rankedChoices } from "@/lib/imports";
import type { ScanImport, StagedChoice } from "@/lib/imports";

export interface ChooseDestinationDialogProps {
  /** The scan row whose candidates are shown; `null` closes the dialog. */
  scan: ScanImport | null;
  busyId: string | null;
  stagedByScan: Record<string, StagedChoice>;
  onStage: (scanId: string, choice: StagedChoice) => void;
  onOpenChange: (open: boolean) => void;
}

/** Modal listing a scan row's ranked destinations; staging one closes it. */
export function ChooseDestinationDialog({
  scan,
  busyId,
  stagedByScan,
  onStage,
  onOpenChange,
}: ChooseDestinationDialogProps) {
  return (
    <Dialog open={scan !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Choose destination</DialogTitle>
        </DialogHeader>
        {scan && (
          <div className="flex flex-col gap-3">
            <p className="text-xs text-muted-foreground">
              Pick a destination, then press Import on the row.
            </p>
            {(scan.result.candidates ?? []).length === 0 &&
            (scan.result.provider_candidates ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">No candidates found.</p>
            ) : (
              <div className="flex max-h-[60vh] flex-col gap-1.5 overflow-y-auto">
                {rankedChoices(scan.result).map((choice) => {
                  const scanId = scan.id;
                  const staged = stagedByScan[scanId];
                  if (choice.kind === "candidate") {
                    const c = choice.data;
                    const isSelected =
                      staged?.kind === "candidate" &&
                      staged.data.media_type === c.media_type &&
                      staged.data.media_id === c.media_id;
                    return (
                      <button
                        key={`e-${c.media_type}-${c.media_id}`}
                        type="button"
                        disabled={busyId === scanId}
                        onClick={() => onStage(scanId, { kind: "candidate", data: c })}
                        aria-pressed={isSelected}
                        className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted ${
                          isSelected
                            ? "border-primary bg-primary/10 hover:bg-primary/15"
                            : "bg-muted/50"
                        }`}
                      >
                        <span className="truncate">
                          {c.media_name}
                          {c.media_year ? ` (${c.media_year})` : ""}
                          <span className="ml-1 text-[10px] text-muted-foreground uppercase">
                            LIBRARY
                          </span>
                        </span>
                        <span className="ml-auto shrink-0">
                          <MatchConfidencePill confidence={c.confidence} breakdown={c.breakdown} />
                        </span>
                      </button>
                    );
                  }
                  const c = choice.data;
                  const isSelected =
                    staged?.kind === "provider" &&
                    staged.data.metadata_provider === c.metadata_provider &&
                    staged.data.external_id === c.external_id;
                  return (
                    <button
                      key={`p-${c.metadata_provider}-${c.external_id}`}
                      type="button"
                      disabled={busyId === scanId}
                      onClick={() => onStage(scanId, { kind: "provider", data: c })}
                      aria-pressed={isSelected}
                      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted ${
                        isSelected
                          ? "border-primary bg-primary/10 hover:bg-primary/15"
                          : "bg-muted/50"
                      }`}
                    >
                      <span className="truncate">
                        {c.name}
                        {c.year ? ` (${c.year})` : ""}
                        <span className="ml-1 text-[10px] text-muted-foreground uppercase">
                          SEARCH
                        </span>
                      </span>
                      <span className="ml-auto shrink-0">
                        <MatchConfidencePill confidence={c.confidence} breakdown={c.breakdown} />
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

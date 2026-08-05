"use client";

import * as React from "react";
import { Check, EllipsisVertical, LoaderCircle, Pencil, RotateCcw, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { isIntegrity, isMedia, isTorrent } from "@/lib/imports";
import type {
  ImportItem,
  IntegrityImport,
  ScanCandidate,
  ScanImport,
  ScanProviderCandidate,
  StagedChoice,
  TorrentImport,
} from "@/lib/imports";

export interface ImportRowActionsProps {
  item: ImportItem;
  busyId: string | null;
  queuedScanIds: Set<string>;
  effectiveChoiceFor: (item: ScanImport) => StagedChoice | null;
  onResolveIntegrity: (item: IntegrityImport, action: "rebaseline" | "dismiss") => void;
  onMapTorrent: (payload: { id: string; title: string }) => void;
  onRetryTorrent: (item: TorrentImport) => void;
  onIgnore: (item: ImportItem) => void;
  onPickScanCandidate: (item: ScanImport, candidate: ScanCandidate) => void;
  onPickProviderCandidate: (item: ScanImport, candidate: ScanProviderCandidate) => void;
  onClearStaged: (scanId: string) => void;
}

/** Trailing row actions for one import row, dispatched by kind and status. */
export function ImportRowActions({
  item: it,
  busyId,
  queuedScanIds,
  effectiveChoiceFor,
  onResolveIntegrity,
  onMapTorrent,
  onRetryTorrent,
  onIgnore,
  onPickScanCandidate,
  onPickProviderCandidate,
  onClearStaged,
}: ImportRowActionsProps): React.ReactNode {
  const busy = busyId === it.id;
  if (isMedia(it)) return null;
  if (isIntegrity(it)) {
    return (
      <>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => onResolveIntegrity(it, "rebaseline")}
          title="Re-baseline the checksum from the file on disk next audit"
        >
          {busy ? (
            <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="mr-1 h-3.5 w-3.5" />
          )}
          Accept current
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="text-muted-foreground"
          disabled={busy}
          onClick={() => onResolveIntegrity(it, "dismiss")}
          title="Keep the original checksum; re-verify next audit"
        >
          <X className="mr-1 h-3.5 w-3.5" />
          Dismiss
        </Button>
      </>
    );
  }
  if (isTorrent(it)) {
    return (
      <>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          title="Map"
          onClick={() => onMapTorrent({ id: it.id, title: it.entry.torrent_title })}
        >
          <Pencil className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground"
          title="Retry"
          disabled={busy}
          onClick={() => onRetryTorrent(it)}
        >
          {busy ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" />
          )}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                <EllipsisVertical className="h-4 w-4" />
              </Button>
            }
          />
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              className="text-destructive"
              disabled={busy}
              onClick={() => onIgnore(it)}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </>
    );
  }
  const r = it.result;
  if (r.status === "imported") {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
              <EllipsisVertical className="h-4 w-4" />
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            className="text-destructive"
            disabled={busy}
            onClick={() => onIgnore(it)}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Ignore
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }
  if (r.status === "queued" || queuedScanIds.has(it.id)) {
    return (
      <>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          Importing
        </div>
        {/* Reserve the trailing ⋮-menu slot so this lines up with the Import button column */}
        <div aria-hidden className="h-7 w-7 shrink-0" />
      </>
    );
  }
  const effective = effectiveChoiceFor(it);
  return (
    <>
      {effective ? (
        <Button
          size="sm"
          data-testid="import-scan-import"
          disabled={busy}
          onClick={() => {
            onClearStaged(it.id);
            if (effective.kind === "candidate") {
              onPickScanCandidate(it, effective.data);
            } else {
              onPickProviderCandidate(it, effective.data);
            }
          }}
        >
          {busy ? <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
          Import
        </Button>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
              <EllipsisVertical className="h-4 w-4" />
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            className="text-destructive"
            data-testid="import-scan-ignore"
            disabled={busy}
            onClick={() => onIgnore(it)}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Ignore
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

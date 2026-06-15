import * as React from "react";
import { Progress } from "@/components/ui/progress";
import { StatusPill } from "@/components/ui/status-pill";
import { MetaPill } from "@/components/ui/type-pill";
import type { ColumnDef } from "@/components/data-list";
import { getTorrentStatusString } from "@/lib/utils";
import type { components } from "@/lib/api/api";

type RichTorrent = components["schemas"]["RichTorrent"];

export function formatSpeed(bytesPerSec: number): string {
  if (bytesPerSec <= 0) return "0 B/s";
  if (bytesPerSec < 1024) return `${bytesPerSec} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
}

/**
 * Progress column: full-width bar while active, then speed (left) ·
 * seeds/peers (center) · percentage (right). Non-active rows show the
 * import progress (n/n imported) full-width left-aligned.
 */
export function torrentProgressColumn(width = "220px"): ColumnDef<RichTorrent> {
  return {
    id: "progress",
    header: "Progress",
    width,
    render: (t) => {
      const status = getTorrentStatusString(t.status);
      const ip = t.import_progress;
      const isActive = status === "Downloading" || status === "Paused";
      const showImport = !isActive && !!ip;

      return (
        <div className="flex w-full min-w-0 flex-col gap-0.5 pr-4">
          {isActive && <Progress value={t.progress ?? 0} className="h-1.5 w-full" />}
          {isActive ? (
            <div className="grid w-full min-w-0 grid-cols-3 items-center text-[11px] text-muted-foreground tabular-nums">
              <span className="truncate text-start">{formatSpeed(t.download_speed ?? 0)}</span>
              <span className="truncate text-center">
                {t.num_seeds ?? 0}S / {t.num_peers ?? 0}P
              </span>
              <span className="truncate text-end">{t.progress ?? 0}%</span>
            </div>
          ) : showImport ? (
            <div className="flex w-full min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
              <MetaPill className="tabular-nums">
                {ip!.imported}/{ip!.total}
              </MetaPill>
              <span className="truncate">imported</span>
            </div>
          ) : null}
        </div>
      );
    },
  };
}

/**
 * Status column: single pill driven only by torrent status, so the same
 * status reads identically across every torrent table.
 */
export function torrentStatusColumn(width = "112px"): ColumnDef<RichTorrent> {
  return {
    id: "status",
    header: "Status",
    width,
    render: (t) => {
      const status = getTorrentStatusString(t.status);
      return <StatusPill status={status} className="shrink-0" />;
    },
  };
}

"use client";

import * as React from "react";
import { EllipsisVertical, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { DataListSection } from "@/components/data-list";
import type { ColumnDef } from "@/components/data-list/types";
import { torrentProgressColumn, torrentStatusColumn } from "@/components/torrents/torrent-columns";
import { MetaPill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SelectionBar } from "@/components/selection-bar";
import {
  formatTorrentSeasonEpisodeLabel,
  getTorrentQualityString,
  getTorrentStatusString,
} from "@/lib/utils";
import type { components } from "@/lib/api/api";

type RichTorrent = components["schemas"]["RichTorrent"];

export type ShowDetailTorrentsListProps = {
  torrents: RichTorrent[];
  isSuperuser: boolean;
  bulkWorking: boolean;
  selectedTorrents: Set<string>;
  allTorrentsSelected: boolean;
  someTorrentsSelected: boolean;
  selectedPausableIds: string[];
  selectedStartableIds: string[];
  onToggleSelected: (id: string, shift: boolean) => void;
  onToggleSelectAll: (checked: boolean) => void;
  onDeselectAll: () => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onRetry: (id: string) => void;
  onDeleteTorrent: (torrent: RichTorrent) => void;
  onBulkPause: (ids: string[]) => void;
  onBulkResume: (ids: string[]) => void;
  onBulkDelete: () => void;
};

export function ShowDetailTorrentsList({
  torrents,
  isSuperuser,
  bulkWorking,
  selectedTorrents,
  allTorrentsSelected,
  someTorrentsSelected,
  selectedPausableIds,
  selectedStartableIds,
  onToggleSelected,
  onToggleSelectAll,
  onDeselectAll,
  onPause,
  onResume,
  onRetry,
  onDeleteTorrent,
  onBulkPause,
  onBulkResume,
  onBulkDelete,
}: ShowDetailTorrentsListProps) {
  const torrentColumns = React.useMemo<ColumnDef<RichTorrent>[]>(
    () => [
      {
        id: "title",
        header: "Torrent",
        width: "minmax(0,1fr)",
        render: (t) => <span className="block truncate pr-4 text-sm font-medium">{t.title}</span>,
      },
      {
        id: "se",
        header: "S / E",
        width: "120px",
        hideBelow: "md",
        render: (t) => {
          const label = formatTorrentSeasonEpisodeLabel(
            t.media?.seasons ?? [],
            t.media?.episodes ?? [],
          );
          return label ? <MetaPill className="font-mono">{label}</MetaPill> : null;
        },
      },
      {
        id: "quality",
        header: "Quality",
        width: "88px",
        render: (t) => (
          <MetaPill className="font-mono">{getTorrentQualityString(t.quality)}</MetaPill>
        ),
      },
      torrentProgressColumn(),
      torrentStatusColumn(),
    ],
    [],
  );

  const torrentRowActions = React.useCallback(
    (t: RichTorrent) => {
      const status = getTorrentStatusString(t.status);
      return (
        <>
          {status === "Downloading" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => onPause(t.id!)}
              title="Pause"
            >
              <Pause className="h-3.5 w-3.5" />
            </Button>
          )}
          {status === "Paused" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => onResume(t.id!)}
              title="Resume"
            >
              <Play className="h-3.5 w-3.5" />
            </Button>
          )}
          {status !== "Finished" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => onRetry(t.id!)}
              title="Retry"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                  <EllipsisVertical className="h-4 w-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              <DropdownMenuItem className="text-destructive" onClick={() => onDeleteTorrent(t)}>
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      );
    },
    [onDeleteTorrent, onPause, onResume, onRetry],
  );

  if (torrents.length === 0) {
    return (
      <div className="col-span-full rounded-lg border border-dashed px-5 py-8 text-center text-sm text-muted-foreground">
        No torrents for this show.
      </div>
    );
  }

  return (
    <>
      <div className="col-span-full">
        <SelectionBar
          allChecked={allTorrentsSelected}
          indeterminate={someTorrentsSelected}
          onAllCheckedChange={onToggleSelectAll}
          onDeselectAll={onDeselectAll}
          summary={
            selectedTorrents.size > 0
              ? `${selectedTorrents.size} torrent${selectedTorrents.size !== 1 ? "s" : ""} selected`
              : "Select all torrents"
          }
          actions={
            <>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onBulkPause(selectedPausableIds)}
                disabled={bulkWorking || selectedPausableIds.length === 0}
              >
                <Pause className="h-4 w-4" />
                Pause
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onBulkResume(selectedStartableIds)}
                disabled={bulkWorking || selectedStartableIds.length === 0}
              >
                <Play className="h-4 w-4" />
                Start
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={onBulkDelete}
                disabled={bulkWorking || selectedTorrents.size === 0}
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </Button>
            </>
          }
        />
      </div>
      <DataListSection<RichTorrent>
        data={torrents}
        getId={(t) => t.id!}
        selectable={isSuperuser}
        selectedIds={selectedTorrents}
        onToggleSelected={onToggleSelected}
        columns={torrentColumns}
        rowActions={torrentRowActions}
      />
    </>
  );
}

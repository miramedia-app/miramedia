"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { ChevronDown, ChevronRight, EllipsisVertical, Trash2 } from "lucide-react";

import { DataListSection } from "@/components/data-list";
import type { ColumnDef } from "@/components/data-list/types";
import { StatusPill } from "@/components/ui/status-pill";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MediaStatusBadge } from "@/components/media-status-badge";
import { DirectDownloadAction } from "@/components/direct-download-action";
import { SearchTorrentButton } from "@/components/download-dialogs/download-media-dialog";
import { SeasonWatchedMenuItems } from "@/components/watchlists/watched-batch-menu";
import { WatchedMenuItems } from "@/components/watchlists/watched-button";
import { AddToWatchlist, AddToWatchlistMenuItem } from "@/components/watchlists/add-to-watchlist";
import { useFeatures } from "@/components/providers/features-provider";
import { formatFileSuffix, getTorrentQualityString } from "@/lib/utils";
import { watchlistOverflowActionsEnabled } from "@/lib/watchlists";
import { languageName } from "@/lib/languages";
import { importedFileRowActions } from "@/lib/media-download";
import type { DeleteTarget, Season, TreeRow } from "@/lib/show-detail";
import type { ShowDetail } from "@/hooks/use-show-detail";

const VideoPlayerDialog = dynamic(
  () => import("@/components/video-player-dialog").then((m) => m.VideoPlayerDialog),
  { ssr: false },
);
const SubtitleSearchDialog = dynamic(
  () =>
    import("@/components/subtitle-search-dialog").then((m) => ({
      default: m.SubtitleSearchDialog,
    })),
  { ssr: false },
);

export interface ShowTreeSectionProps {
  show: ShowDetail;
  isSuperuser: boolean;
  treeRows: TreeRow[];
  allSelectedTreeIds: Set<string>;
  onToggleTreeRowSelected: (id: string) => void;
  onToggleSelectAllTreeRows: (checked: boolean) => void;
  toggleSeason: (seasonId: string) => void;
  toggleEpisode: (episodeId: string) => void;
  toggleSeasonSkipped: (seasonId: string, currentlySkipped: boolean) => void;
  toggleEpisodeSkipped: (episodeId: string, currentlySkipped: boolean) => void;
  subtitlesByEpisode: Record<string, string[]>;
  seasonHasAllSubtitles: (season: Season) => boolean;
  loadSubtitles: () => void;
  openDeleteModal: (target: DeleteTarget) => void;
}

/** Seasons → episodes → files/subtitles tree table with per-row actions. */
export function ShowTreeSection({
  show,
  isSuperuser,
  treeRows,
  allSelectedTreeIds,
  onToggleTreeRowSelected,
  onToggleSelectAllTreeRows,
  toggleSeason,
  toggleEpisode,
  toggleSeasonSkipped,
  toggleEpisodeSkipped,
  subtitlesByEpisode,
  seasonHasAllSubtitles,
  loadSubtitles,
  openDeleteModal,
}: ShowTreeSectionProps) {
  const [watchlistEpisodeId, setWatchlistEpisodeId] = React.useState<string | null>(null);
  const { watchlists, custom_lists, streaming, downloads } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });
  const showOverflowMenu = markWatched || isSuperuser;
  const treeColumns = React.useMemo<ColumnDef<TreeRow>[]>(
    () => [
      {
        id: "title",
        header: "Title",
        width: "minmax(0,1fr)",
        render: (r) => {
          const expandable = r.kind === "season" || r.kind === "episode";
          const isExpanded = (r.kind === "season" || r.kind === "episode") && r.expanded;
          const onChev = (e: React.MouseEvent) => {
            e.stopPropagation();
            if (r.kind === "season") toggleSeason(r.id);
            else if (r.kind === "episode") toggleEpisode(r.id);
          };
          const indentPx = r.depth * 20;
          return (
            <div className="flex min-w-0 items-center gap-2" style={{ paddingLeft: indentPx }}>
              {expandable ? (
                <button
                  type="button"
                  onClick={onChev}
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={isExpanded ? "Collapse" : "Expand"}
                >
                  {isExpanded ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                </button>
              ) : (
                <span className="h-5 w-5 shrink-0" aria-hidden />
              )}

              {r.kind === "season" && (
                <span className="truncate text-sm font-semibold">
                  {r.data.number === 0 ? "Specials" : `Season ${r.data.number}`}
                </span>
              )}
              {r.kind === "episode" && <span className="truncate text-sm">{r.data.title}</span>}
              {r.kind === "file" && (
                <span className="truncate text-sm text-muted-foreground">
                  {r.data.file_name ?? formatFileSuffix(r.data)}
                </span>
              )}
              {r.kind === "subtitle" && (
                <span className="truncate text-sm text-muted-foreground">{r.data.file_name}</span>
              )}
            </div>
          );
        },
      },
      {
        id: "type",
        header: "Type",
        width: "96px",
        render: (r) => {
          if (r.kind === "file") return <TypePill>Video</TypePill>;
          if (r.kind === "subtitle") return <TypePill>Subtitle</TypePill>;
          return null;
        },
      },
      {
        id: "se",
        header: "S/E",
        width: "130px",
        render: (r) => {
          if (r.kind === "season") {
            const done = r.data.episodes.filter((e) => e.downloaded).length;
            return (
              <div className="flex items-center gap-2">
                <MetaPill className="font-mono">S{String(r.data.number).padStart(2, "0")}</MetaPill>
                <MetaPill className="tabular-nums">
                  {done}/{r.data.episodes.length}
                </MetaPill>
              </div>
            );
          }
          if (r.kind === "episode")
            return (
              <MetaPill className="font-mono">
                S{String(r.seasonNumber).padStart(2, "0")}E{String(r.data.number).padStart(2, "0")}
              </MetaPill>
            );
          return null;
        },
      },
      {
        id: "language",
        header: "Language",
        width: "120px",
        render: (r) => {
          const lang =
            r.kind === "subtitle"
              ? r.data.language
              : r.kind === "file"
                ? (show?.original_language ?? null)
                : null;
          return lang ? <MetaPill>{languageName(lang)}</MetaPill> : null;
        },
      },
      {
        id: "quality",
        header: "Quality",
        width: "84px",
        render: (r) =>
          r.kind === "file" ? (
            <MetaPill className="font-mono">{getTorrentQualityString(r.data.quality)}</MetaPill>
          ) : null,
      },
      {
        id: "status",
        header: "Status",
        width: "112px",
        render: (r) => {
          if (r.kind === "season")
            return isSuperuser ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  void toggleSeasonSkipped(r.data.id, !!r.data.skipped);
                }}
              >
                <MediaStatusBadge status={r.data.status} />
              </button>
            ) : (
              <MediaStatusBadge status={r.data.status} />
            );
          if (r.kind === "episode")
            return isSuperuser ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  void toggleEpisodeSkipped(r.data.id, !!r.data.skipped);
                }}
              >
                <MediaStatusBadge status={r.data.status} />
              </button>
            ) : (
              <MediaStatusBadge status={r.data.status} />
            );
          if (r.kind === "file")
            return <StatusPill status={r.data.file_status} className="capitalize" />;
          return <StatusPill status="imported" className="capitalize" />;
        },
      },
    ],
    [
      toggleSeason,
      toggleEpisode,
      isSuperuser,
      show?.original_language,
      toggleSeasonSkipped,
      toggleEpisodeSkipped,
    ],
  );

  return (
    <>
      <DataListSection<TreeRow>
        data={treeRows}
        getId={(r) => r.id}
        selectable={isSuperuser}
        selectedIds={allSelectedTreeIds}
        onToggleSelected={onToggleTreeRowSelected}
        onToggleAllSelected={onToggleSelectAllTreeRows}
        columns={treeColumns}
        rowActions={(r) => {
          if (r.kind === "season") {
            return (
              <>
                {isSuperuser && (
                  <SearchTorrentButton show={show} seasonNumber={r.data.number} iconOnly />
                )}
                <SubtitleSearchDialog
                  mode="show"
                  showId={show.id ?? ""}
                  showName={show.name}
                  seasonNumber={r.data.number}
                  hasAllSubtitles={seasonHasAllSubtitles(r.data)}
                  onUpdate={() => void loadSubtitles()}
                />
                {showOverflowMenu ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground"
                        >
                          <EllipsisVertical className="h-4 w-4" />
                        </Button>
                      }
                    />
                    <DropdownMenuContent align="end">
                      <SeasonWatchedMenuItems showId={show.id ?? ""} seasonNumber={r.data.number} />
                      {isSuperuser && (
                        <DropdownMenuItem
                          className="text-destructive"
                          onClick={() =>
                            openDeleteModal({
                              type: "season",
                              seasonId: r.id,
                            })
                          }
                        >
                          <Trash2 className="size-4" />
                          Delete
                        </DropdownMenuItem>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : null}
              </>
            );
          }
          if (r.kind === "episode") {
            return (
              <>
                {isSuperuser && (
                  <SearchTorrentButton
                    show={show}
                    seasonNumber={r.seasonNumber}
                    episodeNumber={r.data.number}
                    iconOnly
                  />
                )}
                <SubtitleSearchDialog
                  mode="episode"
                  episodeId={r.data.id ?? ""}
                  label={`S${String(r.seasonNumber).padStart(2, "0")}E${String(r.data.number).padStart(2, "0")} ${r.data.title ?? ""}`}
                  hasSubtitles={(subtitlesByEpisode[r.data.id ?? ""] ?? []).length > 0}
                  onUpdate={() => void loadSubtitles()}
                />
                {showOverflowMenu ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground"
                          aria-label="More actions"
                        >
                          <EllipsisVertical className="h-4 w-4" />
                        </Button>
                      }
                    />
                    <DropdownMenuContent align="end">
                      {r.data.id ? (
                        <WatchedMenuItems mediaKind="episode" mediaId={r.data.id} />
                      ) : null}
                      <AddToWatchlistMenuItem
                        onSelect={() => r.data.id && setWatchlistEpisodeId(r.data.id)}
                      />
                      {isSuperuser ? (
                        <DropdownMenuItem
                          className="text-destructive"
                          onClick={() =>
                            openDeleteModal({
                              type: "episode",
                              episodeId: r.data.id,
                              seasonId: r.seasonId,
                            })
                          }
                        >
                          <Trash2 className="size-4" />
                          Delete
                        </DropdownMenuItem>
                      ) : null}
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : null}
              </>
            );
          }
          if (r.kind === "file") {
            const { showPlayer, showDownload } = importedFileRowActions({
              streaming,
              downloads,
              imported: r.data.file_status === "imported",
            });
            return (
              <>
                {showPlayer && (
                  <VideoPlayerDialog
                    mediaType="show"
                    mediaId={r.episodeId}
                    fileId={r.data.id!}
                    title={`S${String(r.seasonNumber).padStart(2, "0")}E${String(r.episodeNumber).padStart(2, "0")} ${r.episodeTitle}`}
                    subtitleLanguages={subtitlesByEpisode[r.episodeId] ?? []}
                    buttonVariant="ghost"
                    buttonSize="icon"
                  />
                )}
                {showDownload && (
                  <DirectDownloadAction
                    mediaType="show"
                    mediaId={r.episodeId}
                    fileId={r.data.id!}
                    buttonVariant="ghost"
                    buttonSize="icon"
                  />
                )}
                {isSuperuser && (
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground"
                        >
                          <EllipsisVertical className="h-4 w-4" />
                        </Button>
                      }
                    />
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        className="text-destructive"
                        onClick={() =>
                          openDeleteModal({
                            type: "file",
                            fileId: r.data.id!,
                          })
                        }
                      >
                        <Trash2 className="size-4" />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </>
            );
          }
          // subtitle
          return isSuperuser ? (
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
                  onClick={() =>
                    openDeleteModal({
                      type: "subtitle",
                      episodeId: r.episodeId,
                      fileName: r.data.file_name,
                    })
                  }
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null;
        }}
      />
      {watchlistEpisodeId ? (
        <AddToWatchlist
          mediaKind="episode"
          mediaId={watchlistEpisodeId}
          open
          hideTrigger
          onOpenChange={(next) => {
            if (!next) setWatchlistEpisodeId(null);
          }}
        />
      ) : null}
    </>
  );
}

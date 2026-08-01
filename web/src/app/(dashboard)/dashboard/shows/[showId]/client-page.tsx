"use client";

import * as React from "react";
import { useRouteUuid } from "@/lib/use-route-id";
import { showDetailBundleQueryOptions } from "@/lib/api/media-queries";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2, EllipsisVertical, ChevronDown, ChevronRight, Check, Ban } from "lucide-react";
import { DataListSection } from "@/components/data-list";
import type { ColumnDef } from "@/components/data-list/types";
import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/ui/status-pill";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import { PageLoader } from "@/components/ui/page-loader";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DashboardHeader } from "@/components/dashboard-header";
import { MediaPicture } from "@/components/media-picture";
import { MediaStatusBadge } from "@/components/media-status-badge";
import { MediaActionsMenu } from "@/components/media-actions-menu";
import { SearchTorrentButton } from "@/components/download-dialogs/download-media-dialog";
import { SelectionBar } from "@/components/selection-bar";
import { useUser } from "@/components/providers/user-provider";
import { useBulkTorrentActions } from "@/hooks/use-bulk-torrent-actions";
import apiClient from "@/lib/api/client";
import { bulkMutate } from "@/lib/bulk-mutate";
import { formatFileSuffix, getTorrentQualityString, getTorrentStatusString } from "@/lib/utils";
import dynamic from "next/dynamic";
const VideoPlayerDialog = dynamic(
  () => import("@/components/video-player-dialog").then((m) => m.VideoPlayerDialog),
  { ssr: false },
);
const ShowSettingsSheet = dynamic(
  () =>
    import("@/components/shows/show-settings-sheet").then((m) => ({
      default: m.ShowSettingsSheet,
    })),
  { ssr: false },
);
const SubtitleSearchDialog = dynamic(
  () =>
    import("@/components/subtitle-search-dialog").then((m) => ({
      default: m.SubtitleSearchDialog,
    })),
  { ssr: false },
);
const ShowDetailTorrentsList = dynamic(
  () =>
    import("@/components/shows/show-detail-torrents-list").then((m) => ({
      default: m.ShowDetailTorrentsList,
    })),
  {
    ssr: false,
    loading: () => <div className="col-span-full h-24 animate-pulse rounded-lg bg-muted/40" />,
  },
);
import { languageName } from "@/lib/languages";
import type { components } from "@/lib/api/api";

type Season = components["schemas"]["PublicSeason"];
type Episode = Season["episodes"][number];
type SubtitleFile = components["schemas"]["SubtitleFile"];
type EpisodeFile = components["schemas"]["PublicEpisodeFile"];
type RichTorrent = components["schemas"]["RichTorrent"];

type TreeRow =
  | { kind: "season"; id: string; depth: 0; data: Season; expanded: boolean }
  | {
      kind: "episode";
      id: string;
      depth: 1;
      data: Episode;
      seasonId: string;
      seasonNumber: number;
      expanded: boolean;
    }
  | {
      kind: "file";
      id: string;
      depth: 2;
      data: EpisodeFile;
      seasonId: string;
      seasonNumber: number;
      episodeId: string;
      episodeNumber: number;
      episodeTitle: string;
    }
  | {
      kind: "subtitle";
      id: string;
      depth: 2;
      data: SubtitleFile;
      seasonId: string;
      episodeId: string;
    };

type DeleteTarget =
  | { type: "file"; fileId: string }
  | { type: "subtitle"; episodeId: string; fileName: string }
  | { type: "episode"; episodeId: string; seasonId: string }
  | { type: "season"; seasonId: string }
  | { type: "torrent"; torrentId: string; torrentName: string }
  | { type: "bulk-files" }
  | { type: "bulk-torrents" };

function fileKey(fileId: string) {
  return `file:${fileId}`;
}
function subKey(episodeId: string, fileName: string) {
  return `${episodeId}:sub:${fileName}`;
}

export default function ShowDetailClientPage() {
  const showId = useRouteUuid("showId");
  const queryClient = useQueryClient();
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;

  // ── Data ───────────────────────────────────────────────────────────────
  // The detail bundle is heavy (full season/episode tree + per-season disk
  // scan + torrents + all-episode subtitles). It must NOT poll on an interval.
  // It refetches on invalidation / SSE only; a staleTime keeps it from
  // re-running on remount churn.
  const bundleQuery = useQuery({
    ...showDetailBundleQueryOptions(showId!),
    enabled: !!showId,
    staleTime: 30 * 1000,
  });

  const show = bundleQuery.data?.show;

  // Live torrent progress comes from a lightweight torrents-only query that
  // polls at 5s ONLY while a download is active. This avoids re-running the
  // heavy bundle for progress updates. Defined inline so media-queries.ts is
  // not touched. Seeded from the bundle so the table renders immediately.
  const bundleTorrents = bundleQuery.data?.torrents;
  const torrentsQuery = useQuery({
    queryKey: ["show", showId, "torrents", "live"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/shows/{show_id}/torrents", {
        signal,
        params: { path: { show_id: showId! } },
      });
      if (error) throw error;
      return (data ?? []) as RichTorrent[];
    },
    enabled: !!showId && bundleTorrents !== undefined,
    initialData: bundleTorrents,
    // The bundle just delivered this exact list, so treat it as fresh for a
    // beat and skip the redundant mount refetch. The refetchInterval predicate
    // still polls at 5s while a torrent is Downloading and SSE/invalidations
    // still force refetches on real changes.
    initialDataUpdatedAt: () => bundleQuery.dataUpdatedAt,
    staleTime: 5_000,
    refetchInterval: (q) => {
      const list = q.state.data ?? [];
      const hasActive = list.some((t) => getTorrentStatusString(t.status) === "Downloading");
      return hasActive ? 5000 : false;
    },
    refetchIntervalInBackground: false,
  });

  const torrents = React.useMemo(
    () => torrentsQuery.data ?? bundleTorrents ?? [],
    [torrentsQuery.data, bundleTorrents],
  );

  const subtitleFilesByEpisode = React.useMemo<Record<string, SubtitleFile[]>>(
    () => bundleQuery.data?.subtitles_by_episode ?? {},
    [bundleQuery.data],
  );

  const subtitlesByEpisode = React.useMemo<Record<string, string[]>>(() => {
    return Object.fromEntries(
      Object.entries(subtitleFilesByEpisode).map(([id, files]) => [
        id,
        [...new Set(files.map((f) => f.language))].sort(),
      ]),
    );
  }, [subtitleFilesByEpisode]);

  const loadSubtitles = React.useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["show", showId] }),
    [queryClient, showId],
  );

  function seasonHasAllSubtitles(season: Season) {
    const downloaded = season.episodes.filter((ep) => ep.downloaded);
    if (downloaded.length === 0) return false;
    return downloaded.every((ep) => (subtitlesByEpisode[ep.id]?.length ?? 0) > 0);
  }

  // ── Expand state ────────────────────────────────────────────────────────
  const [expandedSeasons, setExpandedSeasons] = React.useState<Set<string>>(new Set());
  const [expandedEpisodes, setExpandedEpisodes] = React.useState<Set<string>>(new Set());

  // Season files — one query per expanded season. React Query handles dedup,
  // caching, and per-key invalidation. Stable key order matters for hook
  // call counts, so sort the expanded set.
  const expandedSeasonIds = React.useMemo(
    () => Array.from(expandedSeasons).sort(),
    [expandedSeasons],
  );
  const seasonFileQueries = useQueries({
    queries: expandedSeasonIds.map((seasonId) => ({
      queryKey: ["season-files", seasonId],
      queryFn: async ({ signal }) => {
        const { data } = await apiClient.GET("/api/v1/seasons/{season_id}/files", {
          signal,
          params: { path: { season_id: seasonId } },
        });
        return (data ?? []) as EpisodeFile[];
      },
      staleTime: 60 * 1000,
    })),
  });
  const seasonFilesMap = React.useMemo(() => {
    const map = new Map<string, EpisodeFile[]>();
    expandedSeasonIds.forEach((id, i) => {
      const data = seasonFileQueries[i]?.data;
      if (data) map.set(id, data);
    });
    return map;
  }, [expandedSeasonIds, seasonFileQueries]);

  const getEpisodeFiles = React.useCallback(
    (seasonId: string, episodeId: string) =>
      (seasonFilesMap.get(seasonId) ?? []).filter((f) => f.episode_id === episodeId),
    [seasonFilesMap],
  );

  const invalidateSeasonFiles = React.useCallback(
    (seasonId?: string) =>
      queryClient.invalidateQueries({
        queryKey: seasonId ? ["season-files", seasonId] : ["season-files"],
      }),
    [queryClient],
  );

  const toggleSeason = React.useCallback((seasonId: string) => {
    setExpandedSeasons((prev) => {
      const next = new Set(prev);
      if (next.has(seasonId)) next.delete(seasonId);
      else next.add(seasonId);
      return next;
    });
  }, []);

  const toggleEpisode = React.useCallback((episodeId: string) => {
    setExpandedEpisodes((prev) => {
      const next = new Set(prev);
      if (next.has(episodeId)) next.delete(episodeId);
      else next.add(episodeId);
      return next;
    });
  }, []);

  const invalidateAll = React.useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["show", showId] });
  }, [queryClient, showId]);

  const {
    bulkWorking: torrentBulkWorking,
    pause: bulkPauseTorrents,
    resume: bulkResumeTorrents,
    remove: removeTorrents,
    pauseOne: pauseTorrent,
    resumeOne: resumeTorrent,
    retryOne: retryTorrent,
  } = useBulkTorrentActions(invalidateAll);

  // ── Selection state ─────────────────────────────────────────────────────
  const [selectedSeasons, setSelectedSeasons] = React.useState<Set<string>>(new Set());
  const [selectedEpisodes, setSelectedEpisodes] = React.useState<Set<string>>(new Set());
  const [selectedFiles, setSelectedFiles] = React.useState<Set<string>>(new Set());

  const allSelectedEpisodes = React.useMemo(() => {
    if (!show) return [] as string[];
    const ids = new Set<string>([...selectedEpisodes]);
    for (const seasonId of selectedSeasons) {
      const season = show.seasons.find((s) => s.id === seasonId);
      season?.episodes.forEach((ep) => ids.add(ep.id));
    }
    return [...ids];
  }, [show, selectedSeasons, selectedEpisodes]);

  const hasEpisodeOrSeasonSelection = selectedSeasons.size > 0 || selectedEpisodes.size > 0;
  const hasSelection = hasEpisodeOrSeasonSelection || selectedFiles.size > 0;

  // Derived tree rows for the seasons/episodes/files DataListSection. Hoisted
  // out of the JSX so identity is stable when sibling state changes.
  // Seasons + episodes are already sorted in the queryFn `select`.
  const sortedSeasons = React.useMemo(() => show?.seasons ?? [], [show]);
  const treeRows = React.useMemo<TreeRow[]>(() => {
    const rows: TreeRow[] = [];
    for (const s of sortedSeasons) {
      rows.push({
        kind: "season",
        id: s.id,
        depth: 0,
        data: s,
        expanded: expandedSeasons.has(s.id),
      });
      if (!expandedSeasons.has(s.id)) continue;
      const eps = s.episodes;
      for (const ep of eps) {
        rows.push({
          kind: "episode",
          id: ep.id,
          depth: 1,
          data: ep,
          seasonId: s.id,
          seasonNumber: s.number,
          expanded: expandedEpisodes.has(ep.id),
        });
        if (!expandedEpisodes.has(ep.id)) continue;
        for (const f of getEpisodeFiles(s.id, ep.id)) {
          rows.push({
            kind: "file",
            id: fileKey(f.id!),
            depth: 2,
            data: f,
            seasonId: s.id,
            seasonNumber: s.number,
            episodeId: ep.id,
            episodeNumber: ep.number,
            episodeTitle: ep.title ?? "",
          });
        }
        for (const sub of subtitleFilesByEpisode[ep.id] ?? []) {
          rows.push({
            kind: "subtitle",
            id: subKey(ep.id, sub.file_name),
            depth: 2,
            data: sub,
            seasonId: s.id,
            episodeId: ep.id,
          });
        }
      }
    }
    return rows;
  }, [sortedSeasons, expandedSeasons, expandedEpisodes, getEpisodeFiles, subtitleFilesByEpisode]);

  const allSelectedTreeIds = React.useMemo(
    () => new Set<string>([...selectedSeasons, ...selectedEpisodes, ...selectedFiles]),
    [selectedSeasons, selectedEpisodes, selectedFiles],
  );

  function deselectAll() {
    setSelectedSeasons(new Set());
    setSelectedEpisodes(new Set());
    setSelectedFiles(new Set());
  }

  const allSeasonsSelected =
    !!show && show.seasons.length > 0 && selectedSeasons.size === show.seasons.length;
  const someSeasonsSelected = hasSelection && !allSeasonsSelected;

  function toggleSelectAllSeasons(checked: boolean) {
    if (!show) return;
    if (checked) {
      setSelectedSeasons(new Set(show.seasons.map((s) => s.id)));
    } else {
      deselectAll();
    }
  }

  // ── Torrent selection ──────────────────────────────────────────────────
  const [selectedTorrents, setSelectedTorrents] = React.useState<Set<string>>(new Set());
  const torrentIds = React.useMemo(() => torrents.map((t) => t.id!).filter(Boolean), [torrents]);
  const allTorrentsSelected =
    torrentIds.length > 0 && torrentIds.every((id) => selectedTorrents.has(id));
  const someTorrentsSelected =
    !allTorrentsSelected && torrentIds.some((id) => selectedTorrents.has(id));

  function toggleSelectAllTorrents(checked: boolean) {
    setSelectedTorrents((prev) => {
      const next = new Set(prev);
      if (checked) for (const id of torrentIds) next.add(id);
      else for (const id of torrentIds) next.delete(id);
      return next;
    });
  }

  function toggleTorrentRow(id: string, checked: boolean) {
    setSelectedTorrents((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function bulkDeleteTorrents() {
    const ids = torrentIds.filter((id) => selectedTorrents.has(id));
    if (!ids.length) return;
    await removeTorrents(ids, {
      onResult: ({ failedItems }) => setSelectedTorrents(new Set(failedItems)),
    });
  }

  const pausableTorrents = React.useMemo(
    () => torrents.filter((t) => t.id && t.status === 2),
    [torrents],
  );
  const startableTorrents = React.useMemo(
    () => torrents.filter((t) => t.id && t.status === 3),
    [torrents],
  );
  const selectedPausableIds = pausableTorrents
    .filter((t) => selectedTorrents.has(t.id!))
    .map((t) => t.id!);
  const selectedStartableIds = startableTorrents
    .filter((t) => selectedTorrents.has(t.id!))
    .map((t) => t.id!);

  // ── Bulk actions ────────────────────────────────────────────────────────
  const [otherBulkWorking, setOtherBulkWorking] = React.useState(false);
  const bulkWorking = torrentBulkWorking || otherBulkWorking;

  async function bulkSkip(skipped: boolean) {
    if (!allSelectedEpisodes.length) return;
    setOtherBulkWorking(true);
    try {
      const { ok, failed } = await bulkMutate(allSelectedEpisodes, (id) =>
        apiClient.POST("/api/v1/episodes/{episode_id}/skip", {
          params: { path: { episode_id: id }, query: { skipped } },
        }),
      );
      if (ok > 0) {
        toast.success(
          skipped
            ? `${ok} episode${ok === 1 ? "" : "s"} marked as skipped`
            : `${ok} episode${ok === 1 ? "" : "s"} marked as wanted`,
        );
      }
      if (failed > 0) {
        toast.error(`${failed} episode${failed === 1 ? "" : "s"} could not be updated.`);
      }
      await invalidateAll();
      if (failed === 0) deselectAll();
    } finally {
      setOtherBulkWorking(false);
    }
  }

  async function bulkDeleteFiles() {
    if (!selectedFiles.size) return;
    setOtherBulkWorking(true);
    try {
      const { ok, failed, failedItems } = await bulkMutate([...selectedFiles], (key) => {
        if (key.startsWith("file:")) {
          const fileId = key.slice(5);
          return apiClient.DELETE("/api/v1/episodes/files/{file_id}", {
            params: {
              path: { file_id: fileId },
              query: { delete_from_disk: true },
            },
          });
        }
        // subtitle key: `<episodeId>:sub:<fileName>`
        const colonIdx = key.indexOf(":");
        const episodeId = key.slice(0, colonIdx);
        const fileName = key.slice(colonIdx + 1).replace(/^sub:/, "");
        return apiClient.DELETE("/api/v1/subtitles/episodes/{episode_id}/files", {
          params: {
            path: { episode_id: episodeId },
            query: { file_name: fileName },
          },
        });
      });
      if (failed === 0) {
        toast.success(`${ok} file${ok !== 1 ? "s" : ""} deleted`);
      } else if (ok === 0) {
        toast.error("Failed to delete some files");
      } else {
        toast.warning(`${ok} deleted, ${failed} failed`);
      }
      setSelectedFiles(new Set(failedItems));
      await Promise.all([invalidateSeasonFiles(), invalidateAll()]);
    } finally {
      setOtherBulkWorking(false);
    }
  }

  // ── Delete modal ────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = React.useState<DeleteTarget | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = React.useState("");
  const [deleting, setDeleting] = React.useState(false);
  const deleteConfirmed = deleteConfirmText.toLowerCase() === "delete";

  function openDeleteModal(target: DeleteTarget) {
    setDeleteTarget(target);
    setDeleteConfirmText("");
  }
  function closeDeleteModal() {
    setDeleteTarget(null);
    setDeleteConfirmText("");
  }

  async function confirmDelete() {
    if (!deleteConfirmed || !deleteTarget) return;
    setDeleting(true);
    try {
      const t = deleteTarget;
      if (t.type === "file") {
        const { response } = await apiClient.DELETE("/api/v1/episodes/files/{file_id}", {
          params: {
            path: { file_id: t.fileId },
            query: { delete_from_disk: true },
          },
        });
        if (!response.ok) {
          toast.error("Failed to delete file");
          return;
        }
        toast.success("File deleted");
        setSelectedFiles((prev) => {
          const next = new Set(prev);
          next.delete(fileKey(t.fileId));
          return next;
        });
        await Promise.all([invalidateSeasonFiles(), invalidateAll()]);
      } else if (t.type === "subtitle") {
        const { response } = await apiClient.DELETE(
          "/api/v1/subtitles/episodes/{episode_id}/files",
          {
            params: {
              path: { episode_id: t.episodeId },
              query: { file_name: t.fileName },
            },
          },
        );
        if (!response.ok) {
          toast.error("Failed to delete subtitle");
          return;
        }
        toast.success("Subtitle deleted");
        await loadSubtitles();
        setSelectedFiles((prev) => {
          const next = new Set(prev);
          next.delete(subKey(t.episodeId, t.fileName));
          return next;
        });
      } else if (t.type === "episode") {
        // Bail before deleting files if the skip did not land — otherwise the
        // files are gone while the episode is still wanted, so it is simply
        // re-downloaded.
        const { error: skipError } = await apiClient.POST("/api/v1/episodes/{episode_id}/skip", {
          params: { path: { episode_id: t.episodeId }, query: { skipped: true } },
        });
        if (skipError) {
          toast.error("Failed to skip episode");
          return;
        }
        const files = getEpisodeFiles(t.seasonId, t.episodeId);
        const { failed } = await bulkMutate(files, (f) =>
          apiClient.DELETE("/api/v1/episodes/files/{file_id}", {
            params: {
              path: { file_id: f.id! },
              query: { delete_from_disk: true },
            },
          }),
        );
        if (failed > 0) {
          toast.error(`${failed} episode file${failed === 1 ? "" : "s"} could not be deleted`);
        } else {
          toast.success("Episode files deleted and marked as skipped");
        }
        await Promise.all([invalidateSeasonFiles(t.seasonId), invalidateAll()]);
      } else if (t.type === "season") {
        const { response } = await apiClient.DELETE("/api/v1/seasons/{season_id}", {
          params: { path: { season_id: t.seasonId }, query: { delete_from_disk: true } },
        });
        if (!response.ok) {
          toast.error("Failed to delete season");
          return;
        }
        toast.success("Season files deleted and all episodes marked as skipped");
        await Promise.all([invalidateSeasonFiles(t.seasonId), invalidateAll()]);
      } else if (t.type === "torrent") {
        const { error } = await apiClient.DELETE("/api/v1/torrents/{torrent_id}", {
          params: { path: { torrent_id: t.torrentId } },
        });
        if (error) {
          toast.error("Failed to delete torrent");
          return;
        }
        toast.success("Torrent deleted");
        await invalidateAll();
      } else if (t.type === "bulk-files") {
        await bulkDeleteFiles();
      } else if (t.type === "bulk-torrents") {
        await bulkDeleteTorrents();
      }
      closeDeleteModal();
    } finally {
      setDeleting(false);
    }
  }

  const toggleEpisodeSkipped = React.useCallback(
    async (episodeId: string, currentlySkipped: boolean) => {
      const { response } = await apiClient.POST("/api/v1/episodes/{episode_id}/skip", {
        params: { path: { episode_id: episodeId }, query: { skipped: !currentlySkipped } },
      });
      if (!response.ok) toast.error("Failed to toggle episode skip status.");
      else {
        toast.success(
          currentlySkipped ? "Episode marked as wanted." : "Episode marked as skipped.",
        );
        await invalidateAll();
      }
    },
    [invalidateAll],
  );

  const toggleSeasonSkipped = React.useCallback(
    async (seasonId: string, currentlySkipped: boolean) => {
      const { response } = await apiClient.POST("/api/v1/seasons/{season_id}/skip", {
        params: { path: { season_id: seasonId }, query: { skipped: !currentlySkipped } },
      });
      if (!response.ok) toast.error("Failed to toggle season skip status.");
      else {
        toast.success(currentlySkipped ? "Season marked as wanted." : "Season marked as skipped.");
        await invalidateAll();
      }
    },
    [invalidateAll],
  );

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

  // ── Render ──────────────────────────────────────────────────────────────
  if (!showId) {
    return (
      <>
        <DashboardHeader
          crumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Shows", href: "/dashboard/shows" },
            { label: "Unknown" },
          ]}
        />
        <main className="p-4">
          <PageLoader />
        </main>
      </>
    );
  }

  if (bundleQuery.isError) {
    return (
      <>
        <DashboardHeader
          crumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Shows", href: "/dashboard/shows" },
            { label: "Error" },
          ]}
        />
        <main className="p-4 text-red-500">Error loading show.</main>
      </>
    );
  }

  if (!show) {
    return (
      <>
        <DashboardHeader
          crumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Shows", href: "/dashboard/shows" },
            { label: "Loading…" },
          ]}
        />
        <main className="p-4">
          <PageLoader label="Loading show data…" />
        </main>
      </>
    );
  }

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Shows", href: "/dashboard/shows" },
          { label: show.name },
        ]}
      />
      <main className="flex w-full flex-col gap-6 p-4">
        {/* Hero */}
        <div className="flex flex-col gap-4 md:flex-row md:items-stretch">
          <div className="w-[8.8rem] shrink-0 overflow-hidden rounded-xl md:w-44">
            <MediaPicture media={show} />
          </div>
          <div className="flex flex-1 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <MediaStatusBadge status={show.status ?? (show.skipped ? "skipped" : "wanted")} />
            </div>
            <h1 className="line-clamp-1 text-2xl font-bold tracking-tight">{show.name}</h1>
            {show.content_rating && (
              <Badge variant="outline" className="w-fit font-mono text-xs">
                {show.content_rating}
              </Badge>
            )}
            {show.overview && (
              <p className="mt-1 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                {show.overview}
              </p>
            )}
            {show.genres && show.genres.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {show.genres.map((g) => (
                  <Badge key={g} variant="secondary" className="text-xs">
                    {g}
                  </Badge>
                ))}
              </div>
            )}
            {(() => {
              // Specials (Season 0) are not counted as a season.
              const regularSeasons = show.seasons.filter((s) => s.number !== 0);
              const seasonCount = regularSeasons.length;
              const episodeCount = regularSeasons.reduce((sum, s) => sum + s.episodes.length, 0);
              return (
                <div className="mt-2 text-xs text-muted-foreground">
                  {show.year != null && <>{show.year} &middot; </>}
                  {seasonCount} Season{seasonCount !== 1 ? "s" : ""} &middot; {episodeCount}{" "}
                  Episodes
                </div>
              );
            })()}
            {show.cast && show.cast.length > 0 && (
              <p className="line-clamp-1 text-xs text-muted-foreground">{show.cast.join(", ")}</p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 md:mt-auto md:pt-3">
              <MediaActionsMenu media={show} mediaType="show" />
              {isSuperuser && <ShowSettingsSheet show={show} />}
            </div>
          </div>
        </div>

        {/* Downloads */}
        <div className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">Downloads</h2>
          {isSuperuser && show.seasons.length > 0 && (
            <div className="col-span-full">
              <SelectionBar
                allChecked={allSeasonsSelected}
                indeterminate={someSeasonsSelected}
                onAllCheckedChange={toggleSelectAllSeasons}
                onDeselectAll={deselectAll}
                summary={
                  hasSelection ? (
                    <>
                      {selectedSeasons.size > 0 && (
                        <>
                          {selectedSeasons.size} season{selectedSeasons.size !== 1 ? "s" : ""}
                        </>
                      )}
                      {selectedSeasons.size > 0 && selectedEpisodes.size > 0 && " · "}
                      {selectedEpisodes.size > 0 && (
                        <>
                          {selectedEpisodes.size} episode{selectedEpisodes.size !== 1 ? "s" : ""}
                        </>
                      )}
                      {hasEpisodeOrSeasonSelection && selectedFiles.size > 0 && " · "}
                      {selectedFiles.size > 0 && (
                        <>
                          {selectedFiles.size} file{selectedFiles.size !== 1 ? "s" : ""}
                        </>
                      )}{" "}
                      selected
                    </>
                  ) : (
                    "Select all seasons"
                  )
                }
                actions={
                  <>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => bulkSkip(false)}
                      disabled={bulkWorking || !hasEpisodeOrSeasonSelection}
                    >
                      <Check className="h-4 w-4" />
                      Mark Wanted
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => bulkSkip(true)}
                      disabled={bulkWorking || !hasEpisodeOrSeasonSelection}
                    >
                      <Ban className="h-4 w-4" />
                      Skip
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => openDeleteModal({ type: "bulk-files" })}
                      disabled={bulkWorking || selectedFiles.size === 0}
                    >
                      <Trash2 className="h-4 w-4" />
                      Delete
                    </Button>
                  </>
                }
              />
            </div>
          )}
          {
            <DataListSection<TreeRow>
              data={treeRows}
              getId={(r) => r.id}
              selectable={isSuperuser}
              selectedIds={allSelectedTreeIds}
              onToggleSelected={(id) => {
                const row = treeRows.find((r) => r.id === id);
                if (!row) return;
                const flip = (setter: React.Dispatch<React.SetStateAction<Set<string>>>) =>
                  setter((prev) => {
                    const next = new Set(prev);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  });
                if (row.kind === "season") flip(setSelectedSeasons);
                else if (row.kind === "episode") flip(setSelectedEpisodes);
                else flip(setSelectedFiles);
              }}
              onToggleAllSelected={(checked) => {
                if (checked) {
                  setSelectedSeasons(new Set(sortedSeasons.map((s) => s.id)));
                } else {
                  deselectAll();
                }
              }}
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
                                  type: "season",
                                  seasonId: r.id,
                                })
                              }
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
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
                                  type: "episode",
                                  episodeId: r.data.id,
                                  seasonId: r.seasonId,
                                })
                              }
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </>
                  );
                }
                if (r.kind === "file") {
                  return (
                    <>
                      {r.data.file_status === "imported" && (
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
                              <Trash2 className="mr-2 h-4 w-4" />
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
          }

          {/* Torrents — lazy chunk so first paint skips torrent table code */}
          {isSuperuser && (
            <>
              <h2 className="col-span-full mt-4 text-lg font-semibold">Torrents</h2>
              <ShowDetailTorrentsList
                torrents={torrents}
                isSuperuser={isSuperuser}
                bulkWorking={bulkWorking}
                selectedTorrents={selectedTorrents}
                allTorrentsSelected={allTorrentsSelected}
                someTorrentsSelected={someTorrentsSelected}
                selectedPausableIds={selectedPausableIds}
                selectedStartableIds={selectedStartableIds}
                onToggleSelected={(id, _shift) => toggleTorrentRow(id, !selectedTorrents.has(id))}
                onToggleSelectAll={toggleSelectAllTorrents}
                onDeselectAll={() => setSelectedTorrents(new Set())}
                onPause={pauseTorrent}
                onResume={resumeTorrent}
                onRetry={retryTorrent}
                onDeleteTorrent={(t) =>
                  openDeleteModal({
                    type: "torrent",
                    torrentId: t.id!,
                    torrentName: t.title,
                  })
                }
                onBulkPause={bulkPauseTorrents}
                onBulkResume={bulkResumeTorrents}
                onBulkDelete={() => openDeleteModal({ type: "bulk-torrents" })}
              />
            </>
          )}
        </div>
      </main>

      {/* Delete confirmation modal */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) closeDeleteModal();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {deleteTarget?.type === "season" && "Delete season files?"}
              {deleteTarget?.type === "episode" && "Delete episode files?"}
              {deleteTarget?.type === "subtitle" && "Delete subtitle file?"}
              {deleteTarget?.type === "torrent" && "Delete torrent?"}
              {deleteTarget?.type === "bulk-files" && (
                <>
                  Delete {selectedFiles.size} selected file{selectedFiles.size !== 1 ? "s" : ""}?
                </>
              )}
              {deleteTarget?.type === "bulk-torrents" && (
                <>
                  Delete {selectedTorrents.size} selected torrent
                  {selectedTorrents.size !== 1 ? "s" : ""}?
                </>
              )}
              {deleteTarget?.type === "file" && "Delete file?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.type === "season" &&
                "This will delete all files for this season from disk and mark all episodes as skipped. This cannot be undone."}
              {deleteTarget?.type === "episode" &&
                "This will delete all files for this episode from disk and mark it as skipped. This cannot be undone."}
              {deleteTarget?.type === "torrent" &&
                "This will delete the torrent and its downloaded files. This cannot be undone."}
              {deleteTarget?.type === "bulk-files" &&
                "This will permanently delete the selected files from disk. This cannot be undone."}
              {deleteTarget?.type === "bulk-torrents" &&
                "This will delete the selected torrents and their downloaded files. This cannot be undone."}
              {(deleteTarget?.type === "file" || deleteTarget?.type === "subtitle") &&
                "This will permanently delete the file from disk. This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2 py-2">
            <Label htmlFor="delete-confirm">
              Type <strong>delete</strong> to confirm
            </Label>
            <Input
              id="delete-confirm"
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder="delete"
              autoComplete="off"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={closeDeleteModal}>Cancel</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={!deleteConfirmed || deleting}
              onClick={confirmDelete}
            >
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

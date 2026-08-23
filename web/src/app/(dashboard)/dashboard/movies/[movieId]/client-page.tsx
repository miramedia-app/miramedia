"use client";

import * as React from "react";
import { useRouteUuid } from "@/lib/use-route-id";
import { movieDetailBundleQueryOptions } from "@/lib/api/media-queries";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Ban,
  Check,
  Eye,
  EyeOff,
  Trash2,
  EllipsisVertical,
  Pause,
  Play,
  RotateCcw,
} from "lucide-react";
import { DataListSection } from "@/components/data-list";
import type { ColumnDef } from "@/components/data-list/types";
import { torrentProgressColumn, torrentStatusColumn } from "@/components/torrents/torrent-columns";
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
import { MovieSettingsSheet } from "@/components/movies/movie-settings-sheet";
import { AddToWatchlist } from "@/components/watchlists/add-to-watchlist";
import { WatchedMenuItems } from "@/components/watchlists/watched-button";
import { SelectionBar } from "@/components/selection-bar";
import { useUser } from "@/components/providers/user-provider";
import { DirectDownloadAction } from "@/components/direct-download-action";
import { useFeatures } from "@/components/providers/features-provider";
import { useBulkTorrentActions } from "@/hooks/use-bulk-torrent-actions";
import { useSetWatched } from "@/hooks/use-watched-state";
import apiClient from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { bulkMutate } from "@/lib/bulk-mutate";
import {
  formatCastLine,
  formatFileSuffix,
  getFullyQualifiedMediaName,
  getTorrentQualityString,
  getTorrentStatusString,
} from "@/lib/utils";
import dynamic from "next/dynamic";
const VideoPlayerDialog = dynamic(
  () => import("@/components/video-player-dialog").then((m) => m.VideoPlayerDialog),
  { ssr: false },
);
import { languageName } from "@/lib/languages";
import type { components } from "@/lib/api/api";
import { importedFileRowActions } from "@/lib/media-download";
import { watchlistOverflowActionsEnabled } from "@/lib/watchlists";

type MovieFile = components["schemas"]["PublicMovieFile"];
type SubtitleFile = components["schemas"]["SubtitleFile"];
type RichTorrent = components["schemas"]["RichTorrent"];

type FileRow =
  | { kind: "file"; id: string; data: MovieFile }
  | { kind: "subtitle"; id: string; data: SubtitleFile };

type DeleteTarget =
  | { type: "file"; fileId: string }
  | { type: "subtitle"; fileName: string }
  | { type: "torrent"; torrentId: string; torrentName: string }
  | { type: "bulk-files" }
  | { type: "bulk-torrents" };

function fileKey(fileId: string) {
  return `file:${fileId}`;
}
function subKey(fileName: string) {
  return `sub:${fileName}`;
}

export default function MovieDetailClientPage() {
  const movieId = useRouteUuid("movieId");
  const queryClient = useQueryClient();
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;
  const { watchlists, custom_lists, streaming, downloads } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });

  // The detail bundle is heavy (movie + files + subtitles + torrents). It must
  // NOT poll on an interval. It refetches on invalidation / SSE only; a
  // staleTime keeps it from re-running on remount churn.
  const bundleQuery = useQuery({
    ...movieDetailBundleQueryOptions(movieId!),
    enabled: !!movieId,
    staleTime: 30 * 1000,
  });

  const movie = bundleQuery.data?.movie;
  const movieFiles = React.useMemo(() => bundleQuery.data?.files ?? [], [bundleQuery.data]);
  const subtitleFiles = React.useMemo(() => bundleQuery.data?.subtitles ?? [], [bundleQuery.data]);

  // Live torrent progress comes from a lightweight torrents-only query that
  // polls at 5s ONLY while a download is active. This avoids re-running the
  // heavy bundle for progress updates. Defined inline so media-queries.ts is
  // not touched. Seeded from the bundle so the table renders immediately.
  const bundleTorrents = bundleQuery.data?.movie.torrents;
  const torrentsQuery = useQuery({
    queryKey: ["movie", movieId, "torrents", "live"],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/movies/{movie_id}/torrents", {
        signal,
        params: { path: { movie_id: movieId! } },
      });
      if (error) throw error;
      return (data ?? []) as RichTorrent[];
    },
    enabled: !!movieId && bundleTorrents !== undefined,
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

  // ── File / subtitle rows ────────────────────────────────────────────────
  const fileRows = React.useMemo<FileRow[]>(() => {
    const rows: FileRow[] = [];
    for (const f of movieFiles) {
      rows.push({
        kind: "file",
        id: fileKey(f.id!),
        data: f,
      });
    }
    for (const s of subtitleFiles) {
      rows.push({ kind: "subtitle", id: subKey(s.file_name), data: s });
    }
    return rows;
  }, [movieFiles, subtitleFiles]);

  const subtitleLanguages = React.useMemo(
    () => [...new Set(subtitleFiles.map((s) => s.language))].sort(),
    [subtitleFiles],
  );

  // ── File selection ──────────────────────────────────────────────────────
  const [selectedFiles, setSelectedFiles] = React.useState<Set<string>>(new Set());
  const hasSelection = selectedFiles.size > 0;

  const totalFiles = fileRows.length;
  const allFilesSelected = totalFiles > 0 && selectedFiles.size === totalFiles;
  const someFilesSelected = hasSelection && !allFilesSelected;

  function toggleSelectAllFiles(checked: boolean) {
    if (checked) setSelectedFiles(new Set(fileRows.map((r) => r.id)));
    else setSelectedFiles(new Set());
  }

  function toggleFileRow(id: string) {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // ── Torrent selection ───────────────────────────────────────────────────
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

  // ── Bulk torrent actions ────────────────────────────────────────────────
  const [otherBulkWorking, setOtherBulkWorking] = React.useState(false);

  async function invalidateAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["movie", movieId] }),
      queryClient.invalidateQueries({ queryKey: qk.torrents.list() }),
      queryClient.invalidateQueries({ queryKey: qk.movies.all }),
      queryClient.invalidateQueries({ queryKey: ["dashboard", "summary"] }),
    ]);
  }

  const {
    bulkWorking: torrentBulkWorking,
    pause: bulkPauseTorrents,
    resume: bulkResumeTorrents,
    remove: removeTorrents,
    pauseOne: pauseTorrent,
    resumeOne: resumeTorrent,
    retryOne: retryTorrent,
  } = useBulkTorrentActions(invalidateAll);
  const bulkWorking = torrentBulkWorking || otherBulkWorking;
  const setWatched = useSetWatched();

  async function setMovieSkipped(skipped: boolean) {
    if (!movie?.id) return;
    setOtherBulkWorking(true);
    try {
      const { error } = await apiClient.POST("/api/v1/movies/{movie_id}/skip", {
        params: { path: { movie_id: movie.id }, query: { skipped } },
      });
      if (error) {
        toast.error("Failed to update skip status");
        return;
      }
      toast.success(skipped ? "Movie marked as skipped" : "Movie marked as wanted");
      await invalidateAll();
    } finally {
      setOtherBulkWorking(false);
    }
  }

  async function bulkDeleteTorrents() {
    const ids = torrentIds.filter((id) => selectedTorrents.has(id));
    if (!ids.length) return;
    await removeTorrents(ids, {
      onResult: ({ failedItems }) => setSelectedTorrents(new Set(failedItems)),
    });
  }

  async function bulkDeleteFiles() {
    if (!selectedFiles.size || !movie) return;
    setOtherBulkWorking(true);
    try {
      const { ok, failed, failedItems } = await bulkMutate([...selectedFiles], (key) => {
        if (key.startsWith("sub:")) {
          const fileName = key.slice(4);
          return apiClient.DELETE("/api/v1/subtitles/movies/{movie_id}/files", {
            params: {
              path: { movie_id: movie.id! },
              query: { file_name: fileName },
            },
          });
        } else {
          const fileId = key.slice(5);
          return apiClient.DELETE("/api/v1/movies/{movie_id}/files/{file_id}", {
            params: {
              path: { movie_id: movie.id!, file_id: fileId },
              query: { delete_from_disk: true },
            },
          });
        }
      });
      if (failed === 0) {
        toast.success(`${ok} file${ok !== 1 ? "s" : ""} deleted`);
      } else if (ok === 0) {
        toast.error("Failed to delete some files");
      } else {
        toast.warning(`${ok} deleted, ${failed} failed`);
      }
      setSelectedFiles(new Set(failedItems));
      await invalidateAll();
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
    if (!deleteConfirmed || !deleteTarget || !movie) return;
    setDeleting(true);
    try {
      const t = deleteTarget;
      if (t.type === "file") {
        const { response } = await apiClient.DELETE("/api/v1/movies/{movie_id}/files/{file_id}", {
          params: {
            path: { movie_id: movie.id!, file_id: t.fileId },
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
      } else if (t.type === "subtitle") {
        const { response } = await apiClient.DELETE("/api/v1/subtitles/movies/{movie_id}/files", {
          params: {
            path: { movie_id: movie.id! },
            query: { file_name: t.fileName },
          },
        });
        if (!response.ok) {
          toast.error("Failed to delete subtitle");
          return;
        }
        toast.success("Subtitle deleted");
        setSelectedFiles((prev) => {
          const next = new Set(prev);
          next.delete(subKey(t.fileName));
          return next;
        });
      } else if (t.type === "torrent") {
        const { error } = await apiClient.DELETE("/api/v1/torrents/{torrent_id}", {
          params: { path: { torrent_id: t.torrentId } },
        });
        if (error) {
          toast.error("Failed to delete torrent");
          return;
        }
        toast.success("Torrent deleted");
      } else if (t.type === "bulk-files") {
        await bulkDeleteFiles();
      } else if (t.type === "bulk-torrents") {
        await bulkDeleteTorrents();
      }
      await invalidateAll();
      closeDeleteModal();
    } finally {
      setDeleting(false);
    }
  }

  const fileColumns = React.useMemo<ColumnDef<FileRow>[]>(
    () => [
      {
        id: "title",
        header: "Title",
        width: "minmax(0,1fr)",
        render: (r) => (
          <span className="truncate text-sm text-muted-foreground">
            {r.kind === "file" ? (r.data.file_name ?? formatFileSuffix(r.data)) : r.data.file_name}
          </span>
        ),
      },
      {
        id: "type",
        header: "Type",
        width: "96px",
        render: (r) => <TypePill>{r.kind === "file" ? "Video" : "Subtitle"}</TypePill>,
      },
      {
        id: "language",
        header: "Language",
        width: "120px",
        render: (r) => {
          const lang = r.kind === "subtitle" ? r.data.language : (movie?.original_language ?? null);
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
        render: (r) => (
          <StatusPill
            status={r.kind === "file" ? r.data.file_status : "imported"}
            className="capitalize"
          />
        ),
      },
    ],
    [movie?.original_language],
  );

  const fileRowActions = React.useCallback(
    (r: FileRow) => {
      if (r.kind === "file") {
        const { showPlayer, showDownload } = importedFileRowActions({
          streaming,
          downloads,
          imported: r.data.file_status === "imported",
        });
        return (
          <>
            {showPlayer && movie && (
              <VideoPlayerDialog
                mediaType="movie"
                mediaId={movie.id ?? ""}
                fileId={r.data.id!}
                title={getFullyQualifiedMediaName(movie)}
                subtitleLanguages={subtitleLanguages}
                buttonVariant="ghost"
                buttonSize="icon"
              />
            )}
            {showDownload && movie && (
              <DirectDownloadAction
                mediaType="movie"
                mediaId={movie.id ?? ""}
                fileId={r.data.id!}
                buttonVariant="ghost"
                buttonSize="icon"
              />
            )}
            {(movie?.id && markWatched) || isSuperuser ? (
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
                  {movie?.id ? <WatchedMenuItems mediaKind="movie" mediaId={movie.id} /> : null}
                  {isSuperuser && (
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
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
          </>
        );
      }
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
              onClick={() => openDeleteModal({ type: "subtitle", fileName: r.data.file_name })}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null;
    },
    [movie, subtitleLanguages, isSuperuser, markWatched, streaming, downloads],
  );

  const torrentColumns = React.useMemo<ColumnDef<RichTorrent>[]>(
    () => [
      {
        id: "title",
        header: "Torrent",
        width: "minmax(0,1fr)",
        render: (t) => <span className="block truncate pr-4 text-sm font-medium">{t.title}</span>,
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
              onClick={() => pauseTorrent(t.id!)}
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
              onClick={() => resumeTorrent(t.id!)}
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
              onClick={() => retryTorrent(t.id!)}
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
              <DropdownMenuItem
                className="text-destructive"
                onClick={() =>
                  openDeleteModal({
                    type: "torrent",
                    torrentId: t.id!,
                    torrentName: t.title,
                  })
                }
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  if (!movieId) {
    return (
      <>
        <DashboardHeader
          crumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Movies", href: "/dashboard/movies" },
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
            { label: "Movies", href: "/dashboard/movies" },
            { label: "Error" },
          ]}
        />
        <main className="p-4 text-red-500">Error loading movie.</main>
      </>
    );
  }

  if (!movie) {
    return (
      <>
        <DashboardHeader
          crumbs={[
            { label: "Dashboard", href: "/dashboard" },
            { label: "Movies", href: "/dashboard/movies" },
            { label: "Loading…" },
          ]}
        />
        <main className="p-4">
          <PageLoader label="Loading movie data…" />
        </main>
      </>
    );
  }

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Movies", href: "/dashboard/movies" },
          { label: movie.name },
        ]}
      />
      <main className="flex w-full flex-col gap-6 p-4">
        {/* Hero */}
        <div className="flex flex-col gap-4 md:flex-row md:items-stretch">
          <div className="w-[8.8rem] shrink-0 overflow-hidden rounded-xl md:w-44">
            <MediaPicture media={movie} />
          </div>
          <div className="flex flex-1 flex-col gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <MediaStatusBadge status={movie.status ?? (movie.skipped ? "skipped" : "wanted")} />
              {movie.skipped && <Badge variant="outline">Skipped</Badge>}
            </div>
            <h1 className="line-clamp-1 text-2xl font-bold tracking-tight">{movie.name}</h1>
            {movie.content_rating && (
              <Badge variant="outline" className="w-fit font-mono text-xs">
                {movie.content_rating}
              </Badge>
            )}
            {movie.overview && (
              <p className="mt-1 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                {movie.overview}
              </p>
            )}
            {movie.genres && movie.genres.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {movie.genres.map((g) => (
                  <Badge key={g} variant="secondary" className="text-xs">
                    {g}
                  </Badge>
                ))}
              </div>
            )}
            <div className="mt-1 text-xs text-muted-foreground">
              {movie.year != null && <>{movie.year}</>}
              {movie.year != null && movie.runtime ? " · " : ""}
              {movie.runtime ? `${Math.floor(movie.runtime / 60)}h ${movie.runtime % 60}m` : ""}
            </div>
            {movie.cast && movie.cast.length > 0 && (
              <p className="line-clamp-1 text-xs text-muted-foreground">
                {formatCastLine(movie.cast)}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 md:mt-auto md:pt-3">
              <MediaActionsMenu
                media={movie}
                mediaType="movie"
                afterSubtitles={
                  <AddToWatchlist mediaKind="movie" mediaId={movie.id!} triggerLabel="Watchlists" />
                }
              >
                {isSuperuser ? <MovieSettingsSheet movie={movie} /> : null}
              </MediaActionsMenu>
            </div>
          </div>
        </div>

        {/* Downloads */}
        <div className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">Downloads</h2>
          {isSuperuser && totalFiles > 0 && (
            <SelectionBar
              allChecked={allFilesSelected}
              indeterminate={someFilesSelected}
              onAllCheckedChange={toggleSelectAllFiles}
              onDeselectAll={() => setSelectedFiles(new Set())}
              summary={
                hasSelection
                  ? `${selectedFiles.size} file${selectedFiles.size !== 1 ? "s" : ""} selected`
                  : "Select all files"
              }
              actions={
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setWatched.mutate({
                        media_kind: "movie",
                        media_id: movie.id!,
                        watched: true,
                      })
                    }
                    disabled={bulkWorking || setWatched.isPending}
                  >
                    <Eye className="h-4 w-4" />
                    Watched
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      setWatched.mutate({
                        media_kind: "movie",
                        media_id: movie.id!,
                        watched: false,
                      })
                    }
                    disabled={bulkWorking || setWatched.isPending}
                  >
                    <EyeOff className="h-4 w-4" />
                    Unwatched
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void setMovieSkipped(false)}
                    disabled={bulkWorking}
                  >
                    <Check className="h-4 w-4" />
                    Wanted
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => void setMovieSkipped(true)}
                    disabled={bulkWorking}
                  >
                    <Ban className="h-4 w-4" />
                    Skipped
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
          )}
          <DataListSection<FileRow>
            data={fileRows}
            getId={(r) => r.id}
            selectable={isSuperuser}
            selectedIds={selectedFiles}
            onToggleSelected={(id) => toggleFileRow(id)}
            onToggleAllSelected={toggleSelectAllFiles}
            emptyTitle="No files downloaded yet."
            columns={fileColumns}
            rowActions={fileRowActions}
          />

          {/* Torrents */}
          {isSuperuser && (
            <>
              <h2 className="mt-4 text-lg font-semibold">Torrents</h2>
              {torrents.length > 0 && (
                <SelectionBar
                  allChecked={allTorrentsSelected}
                  indeterminate={someTorrentsSelected}
                  onAllCheckedChange={toggleSelectAllTorrents}
                  onDeselectAll={() => setSelectedTorrents(new Set())}
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
                        onClick={() => bulkPauseTorrents(selectedPausableIds)}
                        disabled={bulkWorking || selectedPausableIds.length === 0}
                      >
                        <Pause className="h-4 w-4" />
                        Pause
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => bulkResumeTorrents(selectedStartableIds)}
                        disabled={bulkWorking || selectedStartableIds.length === 0}
                      >
                        <Play className="h-4 w-4" />
                        Start
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => openDeleteModal({ type: "bulk-torrents" })}
                        disabled={bulkWorking || selectedTorrents.size === 0}
                      >
                        <Trash2 className="h-4 w-4" />
                        Delete
                      </Button>
                    </>
                  }
                />
              )}
              {torrents.length > 0 ? (
                <DataListSection<RichTorrent>
                  data={torrents}
                  getId={(t) => t.id!}
                  selectable={isSuperuser}
                  selectedIds={selectedTorrents}
                  onToggleSelected={(id) => toggleTorrentRow(id, !selectedTorrents.has(id))}
                  columns={torrentColumns}
                  rowActions={torrentRowActions}
                />
              ) : (
                <div className="rounded-lg border border-dashed px-5 py-8 text-center text-sm text-muted-foreground">
                  No torrents for this movie.
                </div>
              )}
            </>
          )}
        </div>
      </main>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) closeDeleteModal();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
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
            <Label htmlFor="delete-confirm-movie">
              Type <strong>delete</strong> to confirm
            </Label>
            <Input
              id="delete-confirm-movie"
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

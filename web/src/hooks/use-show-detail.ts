"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { showDetailBundleQueryOptions } from "@/lib/api/media-queries";
import { qk } from "@/lib/query-keys";
import { useBulkTorrentActions } from "@/hooks/use-bulk-torrent-actions";
import apiClient from "@/lib/api/client";
import { bulkMutate, isAlreadyGone } from "@/lib/bulk-mutate";
import { getTorrentStatusString } from "@/lib/utils";
import {
  invalidateWatchedCaches,
  setSeasonWatched,
  setShowWatched,
} from "@/hooks/use-watched-state";
import {
  buildTreeRows,
  classifyWatchedSelection,
  fileKey,
  seasonHasAllSubtitles as seasonHasAllSubtitlesPure,
  subKey,
  subtitleLanguagesByEpisode,
  withoutFileInSeasonResults,
} from "@/lib/show-detail";
import type {
  DeleteTarget,
  EpisodeFile,
  RichTorrent,
  Season,
  SubtitleFile,
} from "@/lib/show-detail";

/** The season-sorted show object delivered by the detail bundle query. */
export type ShowDetail = NonNullable<
  Awaited<ReturnType<ReturnType<typeof showDetailBundleQueryOptions>["queryFn"]>>
>["show"];

/**
 * Owns the show-detail data layer, expansion, selection, and every mutation
 * (skip, delete, torrent bulk actions). The route client renders the returned
 * contract; query keys, request order, cache invalidation, and confirmation
 * semantics are preserved exactly from the original page.
 */
export function useShowDetail(showId: string | null | undefined) {
  const queryClient = useQueryClient();

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

  const subtitlesByEpisode = React.useMemo<Record<string, string[]>>(
    () => subtitleLanguagesByEpisode(subtitleFilesByEpisode),
    [subtitleFilesByEpisode],
  );

  const loadSubtitles = React.useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["show", showId] }),
    [queryClient, showId],
  );

  const seasonHasAllSubtitles = React.useCallback(
    (season: Season) => seasonHasAllSubtitlesPure(season, subtitlesByEpisode),
    [subtitlesByEpisode],
  );

  // ── Expand state ────────────────────────────────────────────────────────
  const [expandedSeasons, setExpandedSeasons] = React.useState<Set<string>>(new Set());
  const [expandedEpisodes, setExpandedEpisodes] = React.useState<Set<string>>(new Set());

  // Season files — one bounded batch request for all expanded seasons. Per-season
  // cache keys are seeded so narrow invalidation still works.
  const expandedSeasonIds = React.useMemo(
    () => Array.from(expandedSeasons).sort(),
    [expandedSeasons],
  );
  const seasonFilesBatchQuery = useQuery({
    queryKey: ["season-files-batch", showId, expandedSeasonIds],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.POST("/api/v1/seasons/files/batch", {
        signal,
        body: { season_ids: expandedSeasonIds, show_id: showId! },
      });
      if (error) throw error;
      return data;
    },
    enabled: !!showId && expandedSeasonIds.length > 0,
    staleTime: 60 * 1000,
  });

  React.useEffect(() => {
    const results = seasonFilesBatchQuery.data?.results;
    if (!results) return;
    for (const [seasonId, files] of Object.entries(results)) {
      queryClient.setQueryData(["season-files", seasonId], files);
    }
  }, [seasonFilesBatchQuery.data, queryClient]);

  const seasonFilesMap = React.useMemo(() => {
    const map = new Map<string, EpisodeFile[]>();
    const results = seasonFilesBatchQuery.data?.results;
    if (results) {
      for (const [seasonId, files] of Object.entries(results)) {
        map.set(seasonId, files);
      }
    }
    for (const seasonId of expandedSeasonIds) {
      const cached = queryClient.getQueryData<EpisodeFile[]>(["season-files", seasonId]);
      if (cached) map.set(seasonId, cached);
    }
    return map;
  }, [expandedSeasonIds, seasonFilesBatchQuery.data, queryClient]);

  const seasonFilesErrorIds = React.useMemo(() => {
    const failed = new Set<string>();
    const errors = seasonFilesBatchQuery.data?.errors;
    if (errors) {
      for (const seasonId of Object.keys(errors)) failed.add(seasonId);
    }
    if (seasonFilesBatchQuery.isError) {
      expandedSeasonIds.forEach((id) => failed.add(id));
    }
    return failed;
  }, [expandedSeasonIds, seasonFilesBatchQuery.data, seasonFilesBatchQuery.isError]);

  const getEpisodeFiles = React.useCallback(
    (seasonId: string, episodeId: string) =>
      (seasonFilesMap.get(seasonId) ?? []).filter((f) => f.episode_id === episodeId),
    [seasonFilesMap],
  );

  const invalidateSeasonFiles = React.useCallback(
    (seasonId?: string) => {
      void queryClient.invalidateQueries({
        queryKey: seasonId ? ["season-files", seasonId] : ["season-files"],
      });
      void queryClient.invalidateQueries({ queryKey: ["season-files-batch", showId] });
    },
    [queryClient, showId],
  );

  const dropEpisodeFile = React.useCallback(
    (fileId: string) => {
      for (const seasonId of expandedSeasonIds) {
        queryClient.setQueryData<EpisodeFile[]>(["season-files", seasonId], (files) =>
          files?.filter((f) => f.id !== fileId),
        );
      }
      queryClient.setQueryData(
        ["season-files-batch", showId, expandedSeasonIds],
        (
          batch:
            | { results?: Record<string, EpisodeFile[]>; errors?: Record<string, string> }
            | undefined,
        ) => {
          if (!batch?.results) return batch;
          return { ...batch, results: withoutFileInSeasonResults(batch.results, fileId) };
        },
      );
    },
    [queryClient, showId, expandedSeasonIds],
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
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["show", showId] }),
      queryClient.invalidateQueries({ queryKey: qk.torrents.list() }),
      queryClient.invalidateQueries({ queryKey: qk.shows.all }),
      queryClient.invalidateQueries({ queryKey: ["dashboard", "summary"] }),
    ]);
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
  const treeRows = React.useMemo(
    () =>
      buildTreeRows({
        sortedSeasons,
        expandedSeasons,
        expandedEpisodes,
        getEpisodeFiles,
        subtitleFilesByEpisode,
      }),
    [sortedSeasons, expandedSeasons, expandedEpisodes, getEpisodeFiles, subtitleFilesByEpisode],
  );

  const allSelectedTreeIds = React.useMemo(
    () => new Set<string>([...selectedSeasons, ...selectedEpisodes, ...selectedFiles]),
    [selectedSeasons, selectedEpisodes, selectedFiles],
  );

  const deselectAll = React.useCallback(() => {
    setSelectedSeasons(new Set());
    setSelectedEpisodes(new Set());
    setSelectedFiles(new Set());
  }, []);

  const allSeasonsSelected =
    !!show && show.seasons.length > 0 && selectedSeasons.size === show.seasons.length;
  const someSeasonsSelected = hasSelection && !allSeasonsSelected;

  const toggleSelectAllSeasons = React.useCallback(
    (checked: boolean) => {
      if (!show) return;
      if (checked) {
        setSelectedSeasons(new Set(show.seasons.map((s) => s.id)));
      } else {
        deselectAll();
      }
    },
    [show, deselectAll],
  );

  const toggleTreeRowSelected = React.useCallback(
    (id: string) => {
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
    },
    [treeRows],
  );

  const toggleSelectAllTreeRows = React.useCallback(
    (checked: boolean) => {
      if (checked) {
        setSelectedSeasons(new Set(sortedSeasons.map((s) => s.id)));
      } else {
        deselectAll();
      }
    },
    [sortedSeasons, deselectAll],
  );

  // ── Torrent selection ──────────────────────────────────────────────────
  const [selectedTorrents, setSelectedTorrents] = React.useState<Set<string>>(new Set());
  const torrentIds = React.useMemo(() => torrents.map((t) => t.id!).filter(Boolean), [torrents]);
  const allTorrentsSelected =
    torrentIds.length > 0 && torrentIds.every((id) => selectedTorrents.has(id));
  const someTorrentsSelected =
    !allTorrentsSelected && torrentIds.some((id) => selectedTorrents.has(id));

  const toggleSelectAllTorrents = React.useCallback(
    (checked: boolean) => {
      setSelectedTorrents((prev) => {
        const next = new Set(prev);
        if (checked) for (const id of torrentIds) next.add(id);
        else for (const id of torrentIds) next.delete(id);
        return next;
      });
    },
    [torrentIds],
  );

  const toggleTorrentRow = React.useCallback((id: string, checked: boolean) => {
    setSelectedTorrents((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const bulkDeleteTorrents = React.useCallback(async () => {
    const ids = torrentIds.filter((id) => selectedTorrents.has(id));
    if (!ids.length) return;
    await removeTorrents(ids, {
      onResult: ({ failedItems }) => setSelectedTorrents(new Set(failedItems)),
    });
  }, [torrentIds, selectedTorrents, removeTorrents]);

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

  const bulkSkip = React.useCallback(
    async (skipped: boolean) => {
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
    },
    [allSelectedEpisodes, invalidateAll, deselectAll],
  );

  const bulkWatched = React.useCallback(
    async (watched: boolean) => {
      if (!show || !allSelectedEpisodes.length) return;
      setOtherBulkWorking(true);
      try {
        const classified = classifyWatchedSelection(allSelectedEpisodes, show.seasons);
        if (classified.kind !== "episodes") {
          const n = allSelectedEpisodes.length;
          try {
            if (classified.kind === "season") {
              await setSeasonWatched({
                show_id: showId!,
                season_number: classified.seasonNumber,
                watched,
              });
            } else {
              await setShowWatched({ show_id: showId!, watched });
            }
            toast.success(
              watched
                ? `${n} episode${n === 1 ? "" : "s"} marked as watched`
                : `${n} episode${n === 1 ? "" : "s"} marked as unwatched`,
            );
            await invalidateAll();
            await invalidateWatchedCaches(queryClient);
            deselectAll();
          } catch {
            toast.error(`${n} episode${n === 1 ? "" : "s"} could not be updated.`);
          }
          return;
        }

        const { ok, failed } = await bulkMutate(allSelectedEpisodes, (id) =>
          apiClient.PUT("/api/v1/playback/watched", {
            body: { media_kind: "episode", media_id: id, watched },
          }),
        );
        if (ok > 0) {
          toast.success(
            watched
              ? `${ok} episode${ok === 1 ? "" : "s"} marked as watched`
              : `${ok} episode${ok === 1 ? "" : "s"} marked as unwatched`,
          );
        }
        if (failed > 0) {
          toast.error(`${failed} episode${failed === 1 ? "" : "s"} could not be updated.`);
        }
        await invalidateAll();
        await invalidateWatchedCaches(queryClient);
        if (failed === 0) deselectAll();
      } finally {
        setOtherBulkWorking(false);
      }
    },
    [allSelectedEpisodes, show, showId, invalidateAll, deselectAll, queryClient],
  );

  const bulkDeleteFiles = React.useCallback(async () => {
    if (!selectedFiles.size) return;
    setOtherBulkWorking(true);
    try {
      const { ok, failed, failedItems } = await bulkMutate([...selectedFiles], (key) => {
        if (key.startsWith("file:")) {
          const fileId = key.slice(5);
          return apiClient
            .DELETE("/api/v1/episodes/files/{file_id}", {
              params: {
                path: { file_id: fileId },
                query: { delete_from_disk: true },
              },
            })
            .then((result) => (isAlreadyGone(result.response) ? {} : result));
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
  }, [selectedFiles, invalidateSeasonFiles, invalidateAll]);

  // ── Delete modal ────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = React.useState<DeleteTarget | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = React.useState("");
  const [deleting, setDeleting] = React.useState(false);
  const [blockSource, setBlockSource] = React.useState(false);
  const deleteConfirmed = deleteConfirmText.toLowerCase() === "delete";

  const openDeleteModal = React.useCallback((target: DeleteTarget) => {
    setDeleteTarget(target);
    setDeleteConfirmText("");
    setBlockSource(false);
  }, []);
  const closeDeleteModal = React.useCallback(() => {
    setDeleteTarget(null);
    setDeleteConfirmText("");
  }, []);

  const confirmDelete = React.useCallback(async () => {
    if (!deleteConfirmed || !deleteTarget) return;
    setDeleting(true);
    try {
      const t = deleteTarget;
      if (t.type === "file") {
        const { response } = await apiClient.DELETE("/api/v1/episodes/files/{file_id}", {
          params: {
            path: { file_id: t.fileId },
            query: { delete_from_disk: true, block_source: blockSource },
          },
        });
        if (!isAlreadyGone(response)) {
          toast.error("Failed to delete file");
          return;
        }
        toast.success("File deleted");
        setSelectedFiles((prev) => {
          const next = new Set(prev);
          next.delete(fileKey(t.fileId));
          return next;
        });
        dropEpisodeFile(t.fileId);
        void Promise.all([invalidateSeasonFiles(), invalidateAll()]);
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
          apiClient
            .DELETE("/api/v1/episodes/files/{file_id}", {
              params: {
                path: { file_id: f.id! },
                query: { delete_from_disk: true },
              },
            })
            .then((result) => (isAlreadyGone(result.response) ? {} : result)),
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
  }, [
    deleteConfirmed,
    deleteTarget,
    blockSource,
    invalidateSeasonFiles,
    dropEpisodeFile,
    invalidateAll,
    loadSubtitles,
    getEpisodeFiles,
    bulkDeleteFiles,
    bulkDeleteTorrents,
    closeDeleteModal,
  ]);

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

  return {
    // data
    bundleQuery,
    show,
    torrents,
    subtitlesByEpisode,
    loadSubtitles,
    seasonHasAllSubtitles,
    sortedSeasons,
    treeRows,
    getEpisodeFiles,
    seasonFilesErrorIds,
    invalidateSeasonFiles,
    // expansion
    toggleSeason,
    toggleEpisode,
    // tree selection
    allSelectedTreeIds,
    toggleTreeRowSelected,
    toggleSelectAllTreeRows,
    hasSelection,
    hasEpisodeOrSeasonSelection,
    selectedSeasons,
    selectedEpisodes,
    selectedFiles,
    allSeasonsSelected,
    someSeasonsSelected,
    toggleSelectAllSeasons,
    deselectAll,
    // torrent selection
    selectedTorrents,
    setSelectedTorrents,
    allTorrentsSelected,
    someTorrentsSelected,
    selectedPausableIds,
    selectedStartableIds,
    toggleTorrentRow,
    toggleSelectAllTorrents,
    // torrent actions
    pauseTorrent,
    resumeTorrent,
    retryTorrent,
    bulkPauseTorrents,
    bulkResumeTorrents,
    // bulk + skip
    bulkWorking,
    bulkSkip,
    bulkWatched,
    toggleEpisodeSkipped,
    toggleSeasonSkipped,
    // delete modal
    deleteTarget,
    blockSource,
    setBlockSource,
    deleteConfirmText,
    setDeleteConfirmText,
    deleting,
    deleteConfirmed,
    openDeleteModal,
    closeDeleteModal,
    confirmDelete,
  };
}

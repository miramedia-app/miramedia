"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { Ban, Check, Eye, EyeOff, Trash2 } from "lucide-react";

import { useRouteUuid } from "@/lib/use-route-id";
import { Button } from "@/components/ui/button";
import { PageLoader } from "@/components/ui/page-loader";
import { DashboardHeader } from "@/components/dashboard-header";
import { SelectionBar } from "@/components/selection-bar";
import { ShowDetailHero } from "@/components/shows/show-detail-hero";
import { ShowTreeSection } from "@/components/shows/show-tree-section";
import { DeleteConfirmDialog } from "@/components/shows/delete-confirm-dialog";
import { useUser } from "@/components/providers/user-provider";
import { useShowDetail } from "@/hooks/use-show-detail";

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

export default function ShowDetailClientPage() {
  const showId = useRouteUuid("showId");
  const { user } = useUser();
  const isSuperuser = !!user?.is_superuser;

  const detail = useShowDetail(showId);
  const {
    bundleQuery,
    show,
    torrents,
    subtitlesByEpisode,
    loadSubtitles,
    seasonHasAllSubtitles,
    treeRows,
    seasonFilesErrorIds,
    invalidateSeasonFiles,
    toggleSeason,
    toggleEpisode,
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
    selectedTorrents,
    setSelectedTorrents,
    allTorrentsSelected,
    someTorrentsSelected,
    selectedPausableIds,
    selectedStartableIds,
    toggleTorrentRow,
    toggleSelectAllTorrents,
    pauseTorrent,
    resumeTorrent,
    retryTorrent,
    bulkPauseTorrents,
    bulkResumeTorrents,
    bulkWorking,
    bulkSkip,
    bulkWatched,
    toggleEpisodeSkipped,
    toggleSeasonSkipped,
    deleteTarget,
    deleteConfirmText,
    setDeleteConfirmText,
    deleting,
    deleteConfirmed,
    openDeleteModal,
    closeDeleteModal,
    confirmDelete,
  } = detail;

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
        <ShowDetailHero show={show} isSuperuser={isSuperuser} />

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
                      onClick={() => void bulkWatched(true)}
                      disabled={bulkWorking || !hasEpisodeOrSeasonSelection}
                    >
                      <Eye className="h-4 w-4" />
                      Watched
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => void bulkWatched(false)}
                      disabled={bulkWorking || !hasEpisodeOrSeasonSelection}
                    >
                      <EyeOff className="h-4 w-4" />
                      Unwatched
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => bulkSkip(false)}
                      disabled={bulkWorking || !hasEpisodeOrSeasonSelection}
                    >
                      <Check className="h-4 w-4" />
                      Wanted
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => bulkSkip(true)}
                      disabled={bulkWorking || !hasEpisodeOrSeasonSelection}
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
            </div>
          )}
          {seasonFilesErrorIds.size > 0 ? (
            <p role="alert" className="text-sm text-muted-foreground">
              Files for {seasonFilesErrorIds.size} expanded season
              {seasonFilesErrorIds.size === 1 ? "" : "s"} could not be loaded.{" "}
              <Button variant="outline" size="sm" onClick={() => void invalidateSeasonFiles()}>
                Retry
              </Button>
            </p>
          ) : null}
          <ShowTreeSection
            show={show}
            isSuperuser={isSuperuser}
            treeRows={treeRows}
            allSelectedTreeIds={allSelectedTreeIds}
            onToggleTreeRowSelected={toggleTreeRowSelected}
            onToggleSelectAllTreeRows={toggleSelectAllTreeRows}
            toggleSeason={toggleSeason}
            toggleEpisode={toggleEpisode}
            toggleSeasonSkipped={toggleSeasonSkipped}
            toggleEpisodeSkipped={toggleEpisodeSkipped}
            subtitlesByEpisode={subtitlesByEpisode}
            seasonHasAllSubtitles={seasonHasAllSubtitles}
            loadSubtitles={loadSubtitles}
            openDeleteModal={openDeleteModal}
          />

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

      <DeleteConfirmDialog
        target={deleteTarget}
        confirmText={deleteConfirmText}
        onConfirmTextChange={setDeleteConfirmText}
        confirmed={deleteConfirmed}
        deleting={deleting}
        selectedFilesCount={selectedFiles.size}
        selectedTorrentsCount={selectedTorrents.size}
        onClose={closeDeleteModal}
        onConfirm={confirmDelete}
      />
    </>
  );
}

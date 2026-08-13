"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Pause,
  Play,
  RotateCcw,
  MoreVertical,
  Trash2,
  ExternalLink,
  DownloadIcon,
  FilmIcon,
  TvIcon,
  TriangleAlert,
} from "lucide-react";
import { DashboardHeader } from "@/components/dashboard-header";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AddTorrentDialog } from "@/components/torrents/add-torrent-dialog";
import { torrentProgressColumn, torrentStatusColumn } from "@/components/torrents/torrent-columns";
import { DataList, DataListEmpty } from "@/components/data-list";
import type {
  BulkAction,
  ColumnDef,
  FacetDef,
  GroupByDef,
  SortOption,
} from "@/components/data-list";
import { useUser } from "@/components/providers/user-provider";
import { useBulkTorrentActions } from "@/hooks/use-bulk-torrent-actions";
import { useTorrentsList, type RichTorrent } from "@/hooks/use-torrents-list";
import { qk } from "@/lib/query-keys";
import {
  getTorrentQualityString,
  getTorrentStatusString,
  convertTorrentSeasonRangeToIntegerRange,
  convertTorrentEpisodeRangeToIntegerRange,
} from "@/lib/utils";

const getTorrentId = (t: RichTorrent) => t.id!;

function getMediaName(t: RichTorrent): string {
  if (!t.media) return "";
  const name = t.media.media_name;
  const year = t.media.media_year;
  return year ? `${name} (${year})` : name;
}

function getMediaLink(t: RichTorrent): string | null {
  if (!t.media) return null;
  if (t.media.media_type === "show") return `/dashboard/shows/${t.media.media_id}`;
  return `/dashboard/movies/${t.media.media_id}`;
}

const torrentSearchMatch = (t: RichTorrent, q: string) =>
  t.title.toLowerCase().includes(q) || (t.media?.media_name ?? "").toLowerCase().includes(q);

const STATUS_ORDER: Record<string, number> = {
  Downloading: 0,
  Paused: 1,
  Failed: 2,
  Unknown: 3,
  Finished: 4,
};

export default function TorrentsPage() {
  const { user } = useUser();
  const router = useRouter();
  const qc = useQueryClient();
  const searchParams = useSearchParams();
  // Mirror DataList's URL-synced page/pageSize so the query key tracks the
  // server page immediately (including deep links with ?p=).
  const [listPage, setListPage] = React.useState(() => {
    const pageRaw = searchParams.get("p");
    const psRaw = searchParams.get("ps");
    return {
      page: pageRaw ? Math.max(1, Number.parseInt(pageRaw, 10) || 1) : 1,
      pageSize: psRaw ? Math.max(1, Number.parseInt(psRaw, 10) || 50) : 50,
    };
  });
  const onPaginationChange = React.useCallback((next: { page: number; pageSize: number }) => {
    setListPage((prev) =>
      prev.page === next.page && prev.pageSize === next.pageSize ? prev : next,
    );
  }, []);
  const torrentsQuery = useTorrentsList(listPage.page, listPage.pageSize);

  const torrents = React.useMemo(() => torrentsQuery.data?.items ?? [], [torrentsQuery.data]);
  const totalCount = torrentsQuery.data?.total ?? undefined;
  const loadError = torrentsQuery.isError ? "Failed to load torrents" : null;
  // Facets/sort/grouping are client predicates over the loaded page only.
  // With more than one server page they mislead (facet counts, cross-page
  // sort), so offer them only when the whole dataset fits one page.
  // Server-side q/sort/status params are a deferred follow-up.
  const singlePage = totalCount !== undefined && totalCount <= listPage.pageSize;

  const [deleteDialogTorrent, setDeleteDialogTorrent] = React.useState<RichTorrent | null>(null);
  const [blockHash, setBlockHash] = React.useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = React.useState(false);
  const [bulkBlockHash, setBulkBlockHash] = React.useState(false);
  const [pendingBulkDelete, setPendingBulkDelete] = React.useState<RichTorrent[]>([]);
  const invalidateAll = React.useCallback(
    () => qc.invalidateQueries({ queryKey: qk.torrents.all }),
    [qc],
  );
  const {
    bulkWorking,
    pause: pauseTorrents,
    resume: resumeTorrents,
    remove: removeTorrents,
    pauseOne: pauseOneTorrent,
    resumeOne: resumeOneTorrent,
    retryOne: retryOneTorrent,
  } = useBulkTorrentActions(invalidateAll, {
    deleteSuccessPeriod: true,
    failurePeriod: true,
  });

  const bulkPauseTorrents = React.useCallback(
    async (items: RichTorrent[]) => {
      const ids = items.filter((t) => t.id && t.status === 2).map((t) => t.id!);
      if (!ids.length) {
        toast.info("No active downloads in selection");
        return;
      }
      await pauseTorrents(ids);
    },
    [pauseTorrents],
  );

  const bulkResumeTorrents = React.useCallback(
    async (items: RichTorrent[]) => {
      const ids = items.filter((t) => t.id && t.status === 3).map((t) => t.id!);
      if (!ids.length) {
        toast.info("No paused torrents in selection");
        return;
      }
      await resumeTorrents(ids);
    },
    [resumeTorrents],
  );

  async function bulkDelete() {
    const ids = pendingBulkDelete.map((t) => t.id!).filter(Boolean);
    if (!ids.length) return;
    await removeTorrents(ids, {
      blockHash: bulkBlockHash,
      onResult: ({ failed, failedItems }) => {
        if (failed === 0) {
          setBulkDeleteOpen(false);
          setBulkBlockHash(false);
          setPendingBulkDelete([]);
        } else {
          const failedSet = new Set(failedItems);
          setPendingBulkDelete((prev) => prev.filter((t) => t.id && failedSet.has(t.id)));
        }
      },
    });
  }

  // Single-torrent actions live on the hook so their toasts and invalidation
  // can't drift from the bulk paths.
  const retryTorrentDownload = React.useCallback(
    (t: RichTorrent) => retryOneTorrent(t.id!),
    [retryOneTorrent],
  );

  const pauseTorrent = React.useCallback(
    (t: RichTorrent) => pauseOneTorrent(t.id!),
    [pauseOneTorrent],
  );

  const resumeTorrent = React.useCallback(
    (t: RichTorrent) => resumeOneTorrent(t.id!),
    [resumeOneTorrent],
  );

  async function confirmDelete() {
    if (!deleteDialogTorrent) return;
    // Route through the shared hook so the toast + invalidation match the bulk
    // path. Close the dialog either way (matching bulk delete, which dismisses
    // its confirm UI once the request settles); the hook owns error reporting.
    await removeTorrents([deleteDialogTorrent.id!], { blockHash });
    setDeleteDialogTorrent(null);
    setBlockHash(false);
  }

  // DataList configuration
  const columns = React.useMemo<ColumnDef<RichTorrent>[]>(
    () => [
      {
        id: "title",
        header: "Title",
        width: "minmax(0,1fr)",
        render: (t) => {
          const mediaLink = getMediaLink(t);
          return (
            <div className="flex min-w-0 flex-col gap-0.5 pr-4">
              {mediaLink ? (
                <Link
                  href={mediaLink}
                  onClick={(e) => e.stopPropagation()}
                  className="truncate text-sm font-medium hover:underline"
                >
                  {getMediaName(t)}
                </Link>
              ) : (
                <span className="truncate text-sm text-muted-foreground">Unlinked</span>
              )}
              <span className="truncate text-xs text-muted-foreground">{t.title}</span>
            </div>
          );
        },
      },
      {
        id: "type",
        header: "Type",
        width: "72px",
        render: (t) => <TypePill>{t.media?.media_type === "show" ? "Show" : "Movie"}</TypePill>,
      },
      {
        id: "season-episode",
        header: "S / E",
        width: "120px",
        hideBelow: "md",
        render: (t) => {
          const eps = t.media?.episodes ?? [];
          const seasons = t.media?.seasons ?? [];
          const isFullSeason = eps.length > 1 && eps[0] === 1 && eps.every((e, i) => e === i + 1);
          if (seasons.length === 0) return null;
          return (
            <div className="flex items-center gap-1">
              <MetaPill className="font-mono">
                S{convertTorrentSeasonRangeToIntegerRange(seasons)}
              </MetaPill>
              {eps.length > 0 && !isFullSeason && (
                <MetaPill className="font-mono">
                  E{convertTorrentEpisodeRangeToIntegerRange(eps)}
                </MetaPill>
              )}
            </div>
          );
        },
      },
      {
        id: "quality",
        header: "Quality",
        width: "88px",
        hideBelow: "sm",
        render: (t) => (
          <MetaPill className="font-mono">{getTorrentQualityString(t.quality)}</MetaPill>
        ),
      },
      torrentProgressColumn(),
      torrentStatusColumn(),
    ],
    [],
  );

  const facets = React.useMemo<FacetDef<RichTorrent>[]>(
    () => [
      {
        id: "type",
        label: "Type",
        options: [
          { value: "show", label: "Show", icon: <TvIcon className="h-3.5 w-3.5" /> },
          { value: "movie", label: "Movie", icon: <FilmIcon className="h-3.5 w-3.5" /> },
        ],
        predicate: (t, values, op) => {
          const v = t.media?.media_type ?? "unknown";
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "status",
        label: "Status",
        options: [
          { value: "Downloading", label: "Downloading" },
          { value: "Paused", label: "Paused" },
          { value: "Finished", label: "Finished" },
          { value: "Failed", label: "Failed" },
          { value: "Unknown", label: "Unknown" },
        ],
        predicate: (t, values, op) => {
          const v = getTorrentStatusString(t.status);
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
      {
        id: "quality",
        label: "Quality",
        options: [
          { value: "4K", label: "4K" },
          { value: "1080p", label: "1080p" },
          { value: "720p", label: "720p" },
          { value: "SD", label: "SD" },
          { value: "Unknown", label: "Unknown" },
        ],
        predicate: (t, values, op) => {
          const v = getTorrentQualityString(t.quality);
          const hit = values.includes(v);
          return op === "excludes" ? !hit : hit;
        },
      },
    ],
    [],
  );

  const sortOptions = React.useMemo<SortOption<RichTorrent>[]>(
    () => [
      { id: "title-asc", label: "Title A–Z", compare: (a, b) => a.title.localeCompare(b.title) },
      { id: "title-desc", label: "Title Z–A", compare: (a, b) => b.title.localeCompare(a.title) },
      {
        id: "media-asc",
        label: "Media A–Z",
        compare: (a, b) => (a.media?.media_name ?? "").localeCompare(b.media?.media_name ?? ""),
      },
      {
        id: "media-desc",
        label: "Media Z–A",
        compare: (a, b) => (b.media?.media_name ?? "").localeCompare(a.media?.media_name ?? ""),
      },
      {
        id: "progress-desc",
        label: "Progress (high)",
        compare: (a, b) => (b.progress ?? 0) - (a.progress ?? 0),
      },
    ],
    [],
  );

  const groupings = React.useMemo<GroupByDef<RichTorrent>[]>(
    () => [
      {
        id: "status",
        label: "Status",
        getGroup: (t) => {
          const s = getTorrentStatusString(t.status);
          return { key: s, label: s, sortOrder: STATUS_ORDER[s] ?? 99 };
        },
      },
      {
        id: "type",
        label: "Media type",
        getGroup: (t) => {
          const k = t.media?.media_type ?? "unknown";
          return { key: k, label: k === "show" ? "Shows" : k === "movie" ? "Movies" : "Unlinked" };
        },
      },
      {
        id: "quality",
        label: "Quality",
        getGroup: (t) => {
          const k = getTorrentQualityString(t.quality);
          return { key: k, label: k };
        },
      },
    ],
    [],
  );

  const bulkActions = React.useMemo<BulkAction<RichTorrent>[]>(
    () =>
      user?.is_superuser
        ? [
            {
              id: "pause",
              label: "Pause",
              icon: <Pause className="h-3.5 w-3.5" />,
              variant: "secondary",
              disabled: bulkWorking,
              onRun: bulkPauseTorrents,
            },
            {
              id: "resume",
              label: "Resume",
              icon: <Play className="h-3.5 w-3.5" />,
              variant: "secondary",
              disabled: bulkWorking,
              onRun: bulkResumeTorrents,
            },
            {
              id: "delete",
              label: "Delete",
              icon: <Trash2 className="h-3.5 w-3.5" />,
              variant: "destructive",
              disabled: bulkWorking,
              onRun: (items) => {
                setPendingBulkDelete(items);
                setBulkDeleteOpen(true);
              },
            },
          ]
        : [],
    [user?.is_superuser, bulkWorking, bulkPauseTorrents, bulkResumeTorrents],
  );

  const renderRowActions = React.useCallback(
    (t: RichTorrent) => {
      const status = getTorrentStatusString(t.status);
      const mediaLink = getMediaLink(t);
      return (
        <>
          {user?.is_superuser && status === "Downloading" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => void pauseTorrent(t)}
              title="Pause"
            >
              <Pause className="h-3.5 w-3.5" />
            </Button>
          )}
          {user?.is_superuser && status === "Paused" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => void resumeTorrent(t)}
              title="Resume"
            >
              <Play className="h-3.5 w-3.5" />
            </Button>
          )}
          {user?.is_superuser && status !== "Finished" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              onClick={() => void retryTorrentDownload(t)}
              title="Retry"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground">
                  <MoreVertical className="h-4 w-4" />
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              {mediaLink && (
                <DropdownMenuItem onClick={() => router.push(mediaLink)}>
                  <ExternalLink className="mr-2 h-4 w-4" />
                  View Media
                </DropdownMenuItem>
              )}
              {user?.is_superuser && (
                <DropdownMenuItem
                  className="text-destructive"
                  onClick={() => setDeleteDialogTorrent(t)}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      );
    },
    [user?.is_superuser, router, pauseTorrent, resumeTorrent, retryTorrentDownload],
  );

  return (
    <>
      <DashboardHeader
        crumbs={[{ label: "Dashboard", href: "/dashboard" }, { label: "Torrents" }]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        {loadError ? (
          <DataListEmpty
            icon={<TriangleAlert />}
            title={loadError}
            description="Check that the backend is reachable, then retry."
            action={
              <Button variant="outline" size="sm" onClick={() => torrentsQuery.refetch()}>
                Retry
              </Button>
            }
          />
        ) : (
          <DataList<RichTorrent>
            data={torrents}
            getId={getTorrentId}
            columns={columns}
            pageSize={50}
            totalCount={totalCount}
            onPaginationChange={onPaginationChange}
            searchPlaceholder="Search or filter torrents…"
            searchMatch={torrentSearchMatch}
            facets={singlePage ? facets : undefined}
            sortOptions={singlePage ? sortOptions : undefined}
            defaultSort={singlePage ? "title-asc" : undefined}
            groupings={singlePage ? groupings : undefined}
            defaultGroupId={singlePage ? "status" : undefined}
            collapseStorageKey="torrents"
            bulkActions={bulkActions}
            loading={torrentsQuery.isLoading}
            density="rich"
            emptyIcon={<DownloadIcon />}
            emptyTitle="No torrents yet"
            emptyDescription="Search a show or movie to start downloading."
            toolbarTrailing={user?.is_superuser ? <AddTorrentDialog /> : null}
            rowActions={renderRowActions}
          />
        )}
      </main>

      <Dialog
        open={deleteDialogTorrent !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteDialogTorrent(null);
            setBlockHash(false);
          }
        }}
      >
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>Delete torrent</DialogTitle>
            <DialogDescription>
              Removes the download client&apos;s working copy. Library files (hardlinked) are
              preserved. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div
            className="truncate text-sm font-medium"
            title={deleteDialogTorrent?.title ?? undefined}
          >
            {deleteDialogTorrent?.title}
          </div>
          <div className="flex items-start gap-3 rounded-md border bg-muted/30 p-3">
            <Checkbox
              id="block-hash"
              checked={blockHash}
              onCheckedChange={(v) => setBlockHash(v === true)}
              className="mt-0.5"
            />
            <div className="grid gap-1 leading-tight">
              <Label htmlFor="block-hash" className="text-sm font-medium">
                Add to deny-list
              </Label>
              <p className="text-xs text-muted-foreground">
                Prevents this release (by info-hash) from being re-queued by auto-download or manual
                search.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setDeleteDialogTorrent(null);
                setBlockHash(false);
              }}
            >
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={bulkWorking}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => {
          if (!open) {
            setBulkDeleteOpen(false);
            setBulkBlockHash(false);
            setPendingBulkDelete([]);
          }
        }}
      >
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>
              Delete {pendingBulkDelete.length} torrent
              {pendingBulkDelete.length !== 1 ? "s" : ""}
            </DialogTitle>
            <DialogDescription>
              Removes the download client&apos;s working copy for each selected torrent. Library
              files (hardlinked) are preserved. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-start gap-3 rounded-md border bg-muted/30 p-3">
            <Checkbox
              id="bulk-block-hash"
              checked={bulkBlockHash}
              onCheckedChange={(v) => setBulkBlockHash(v === true)}
              className="mt-0.5"
            />
            <div className="grid gap-1 leading-tight">
              <Label htmlFor="bulk-block-hash" className="text-sm font-medium">
                Add to deny-list
              </Label>
              <p className="text-xs text-muted-foreground">
                Prevents these releases (by info-hash) from being re-queued.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setBulkDeleteOpen(false);
                setBulkBlockHash(false);
                setPendingBulkDelete([]);
              }}
              disabled={bulkWorking}
            >
              Cancel
            </Button>
            <Button variant="destructive" onClick={bulkDelete} disabled={bulkWorking}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

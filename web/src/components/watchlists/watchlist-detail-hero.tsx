"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ListChecks, Trash2 } from "lucide-react";

import { MediaPicture } from "@/components/media-picture";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { WatchlistSettingsSheet } from "@/components/watchlists/watchlist-settings-sheet";
import { WATCHLISTS_BASE } from "@/components/watchlists/watchlists-routes";
import { useDeleteWatchlist } from "@/hooks/use-watchlists";
import { cn } from "@/lib/utils";
import type { WatchlistDetail } from "@/lib/watchlists";

export const WATCHLIST_NO_DESCRIPTION = "No description yet. Open Settings to add one.";

function WatchlistDeleteButton({ watchlistId, name }: { watchlistId: string; name: string }) {
  const router = useRouter();
  const deleteWatchlist = useDeleteWatchlist();
  const [deleteOpen, setDeleteOpen] = React.useState(false);

  async function handleDelete() {
    await deleteWatchlist.mutateAsync(watchlistId);
    setDeleteOpen(false);
    router.replace(WATCHLISTS_BASE);
  }

  return (
    <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
      <AlertDialogTrigger
        className={cn(
          buttonVariants({ variant: "destructive", size: "sm" }),
          "border-destructive/30",
        )}
      >
        <Trash2 className="size-4" />
        Delete
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {name}?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes the list. Items in your library are not affected.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={deleteWatchlist.isPending}
            onClick={() => void handleDelete()}
          >
            Delete
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function WatchlistDetailHero({ detail }: { detail: WatchlistDetail }) {
  const coverId = detail.items[0]?.poster_media_id ?? null;
  const itemCount = detail.items.length;
  const overview = detail.description?.trim() ? detail.description : WATCHLIST_NO_DESCRIPTION;

  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-stretch">
      <div className="w-[8.8rem] shrink-0 overflow-hidden rounded-xl md:w-44">
        {coverId ? (
          <MediaPicture media={{ id: coverId, name: detail.name, year: null }} priority />
        ) : (
          <div
            className="flex aspect-[2/3] w-full items-center justify-center rounded-xl bg-muted"
            role="img"
            aria-label={`${detail.name} cover`}
          >
            <ListChecks className="size-12 text-muted-foreground" />
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2">
        <h1 className="line-clamp-2 text-2xl font-bold tracking-tight text-balance">
          {detail.name}
        </h1>
        <p
          className={
            detail.description?.trim()
              ? "mt-1 line-clamp-3 text-sm leading-relaxed text-pretty text-muted-foreground"
              : "mt-1 line-clamp-3 text-sm leading-relaxed text-pretty text-muted-foreground/80 italic"
          }
        >
          {overview}
        </p>
        <div className="mt-2 text-xs text-muted-foreground tabular-nums">
          {itemCount} item{itemCount === 1 ? "" : "s"}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 md:mt-auto md:pt-3">
          <div className="flex items-center gap-2">
            <WatchlistSettingsSheet
              watchlistId={detail.id}
              name={detail.name}
              description={detail.description}
            />
            <WatchlistDeleteButton watchlistId={detail.id} name={detail.name} />
          </div>
        </div>
      </div>
    </div>
  );
}

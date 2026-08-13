"use client";

import * as React from "react";
import { Check, EllipsisVertical, Eye, EyeOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  countDownloadedEpisodes,
  showUnwatchedNeedsConfirmation,
  useSetSeasonWatched,
  useSetShowWatched,
} from "@/hooks/use-watched-state";
import { useFeatures } from "@/components/providers/features-provider";
import { getWatchedButtonA11y } from "@/components/watchlists/watched-button";
import { watchlistOverflowActionsEnabled } from "@/lib/watchlists";

export function ShowWatchedMenu({
  showId,
  seasons,
}: {
  showId: string;
  seasons: { episodes: { downloaded?: boolean | null }[] }[];
}) {
  const { watchlists, custom_lists } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });
  const setShowWatched = useSetShowWatched();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const downloadedCount = countDownloadedEpisodes(seasons);

  function markShow(watched: boolean) {
    if (!watched && showUnwatchedNeedsConfirmation(false, downloadedCount)) {
      setConfirmOpen(true);
      return;
    }
    setShowWatched.mutate({ show_id: showId, watched });
  }

  if (!markWatched) return null;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="outline" size="sm">
              <EllipsisVertical className="h-4 w-4" />
              Watched
            </Button>
          }
        />
        <DropdownMenuContent align="start">
          <DropdownMenuItem onClick={() => markShow(true)}>
            <Check className="mr-2 h-4 w-4" />
            Mark show watched
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => markShow(false)}>
            <Check className="mr-2 h-4 w-4 opacity-30" />
            Mark show unwatched
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Mark entire show unwatched?</AlertDialogTitle>
            <AlertDialogDescription>
              This will clear watched status for {downloadedCount} downloaded episodes in this show.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              onClick={() => {
                setShowWatched.mutate({ show_id: showId, watched: false });
                setConfirmOpen(false);
              }}
              disabled={setShowWatched.isPending}
            >
              Mark unwatched
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function SeasonWatchedMenuItems({
  showId,
  seasonNumber,
}: {
  showId: string;
  seasonNumber: number;
}) {
  const { watchlists, custom_lists } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });
  const setSeasonWatched = useSetSeasonWatched();
  const pending = setSeasonWatched.isPending;

  if (!markWatched) return null;

  return (
    <>
      <DropdownMenuItem
        disabled={pending}
        onClick={() =>
          setSeasonWatched.mutate({
            show_id: showId,
            season_number: seasonNumber,
            watched: true,
          })
        }
      >
        <Eye className="size-4" />
        {getWatchedButtonA11y(false).label}
      </DropdownMenuItem>
      <DropdownMenuItem
        disabled={pending}
        onClick={() =>
          setSeasonWatched.mutate({
            show_id: showId,
            season_number: seasonNumber,
            watched: false,
          })
        }
      >
        <EyeOff className="size-4" />
        {getWatchedButtonA11y(true).label}
      </DropdownMenuItem>
    </>
  );
}

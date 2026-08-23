"use client";

import { Eye, EyeOff } from "lucide-react";

import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useFeatures } from "@/components/providers/features-provider";
import { useSetWatched } from "@/hooks/use-watched-state";
import type { components } from "@/lib/api/api";
import { watchlistOverflowActionsEnabled } from "@/lib/watchlists";

type MediaKind = components["schemas"]["MediaKind"];

export function getWatchedButtonA11y(watched: boolean) {
  return {
    "aria-pressed": watched,
    label: watched ? "Mark unwatched" : "Mark watched",
  } as const;
}

export function WatchedMenuItems({
  mediaKind,
  mediaId,
}: {
  mediaKind: MediaKind;
  mediaId: string;
}) {
  const { watchlists, custom_lists } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });
  const setWatched = useSetWatched();
  const pending = setWatched.isPending;

  function mark(next: boolean) {
    setWatched.mutate({
      media_kind: mediaKind,
      media_id: mediaId,
      watched: next,
    });
  }

  if (!markWatched) return null;

  return (
    <>
      <DropdownMenuItem disabled={pending} onClick={() => mark(true)}>
        <Eye className="size-4" />
        {getWatchedButtonA11y(false).label}
      </DropdownMenuItem>
      <DropdownMenuItem disabled={pending} onClick={() => mark(false)}>
        <EyeOff className="size-4" />
        {getWatchedButtonA11y(true).label}
      </DropdownMenuItem>
    </>
  );
}

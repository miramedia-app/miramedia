"use client";

import { Check, Eye, EyeOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useFeatures } from "@/components/providers/features-provider";
import { useSetWatched, useWatchedState } from "@/hooks/use-watched-state";
import type { components } from "@/lib/api/api";
import { watchlistOverflowActionsEnabled } from "@/lib/watchlists";
import { cn } from "@/lib/utils";

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

export function WatchedButton({
  mediaKind,
  mediaId,
  className,
  size = "sm",
  variant = "outline",
}: {
  mediaKind: MediaKind;
  mediaId: string;
  className?: string;
  size?: "sm" | "default" | "icon";
  variant?: "outline" | "ghost" | "default";
}) {
  const { watchlists, custom_lists } = useFeatures();
  const { markWatched } = watchlistOverflowActionsEnabled({ watchlists, custom_lists });
  const { data, isPending: queryPending } = useWatchedState(mediaKind, mediaId);
  const setWatched = useSetWatched();
  const watched = !!data?.watched;
  const pending = queryPending || setWatched.isPending;
  const a11y = getWatchedButtonA11y(watched);

  function handleClick() {
    setWatched.mutate({
      media_kind: mediaKind,
      media_id: mediaId,
      watched: !watched,
    });
  }

  if (!markWatched) return null;

  if (size === "icon") {
    return (
      <Button
        type="button"
        variant={variant}
        size="icon"
        className={cn("h-7 w-7", className)}
        aria-pressed={a11y["aria-pressed"]}
        aria-label={a11y.label}
        title={a11y.label}
        disabled={pending}
        onClick={handleClick}
      >
        <Check className={cn("h-3.5 w-3.5", watched ? "opacity-100" : "opacity-30")} aria-hidden />
      </Button>
    );
  }

  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      className={className}
      aria-pressed={a11y["aria-pressed"]}
      disabled={pending}
      onClick={handleClick}
    >
      <Check className={cn("h-4 w-4", watched ? "opacity-100" : "opacity-30")} aria-hidden />
      {a11y.label}
    </Button>
  );
}

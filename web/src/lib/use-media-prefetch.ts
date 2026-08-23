"use client";

// Hover/focus prefetch for media detail pages.
//
// The library list endpoints return summary rows for fast grids. On strong
// intent (hover or keyboard focus), prefetch the real detail payload and warm
// the route chunk so navigation still feels instant without making the list
// response carry every season/episode/torrent.

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  movieDetailBundleQueryOptions,
  showDetailBundleQueryOptions,
} from "@/lib/api/media-queries";
import type { components } from "@/lib/api/api";

type Show = components["schemas"]["PublicShow"];
type Movie = components["schemas"]["PublicMovie"];

export const DETAIL_BUNDLE_PREFETCH_INTENT_MS = 150;

export function createHoverIntent(delayMs = DETAIL_BUNDLE_PREFETCH_INTENT_MS) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return {
    schedule(run: () => void) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        run();
      }, delayMs);
    },
    cancel() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}

export function useShowPrefetch() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const intentRef = React.useRef(createHoverIntent());
  React.useEffect(() => () => intentRef.current.cancel(), []);
  const prefetch = React.useCallback(
    (show: Show) => {
      const id = show.id;
      if (!id) return;
      router.prefetch(`/dashboard/shows/${id}`);
      intentRef.current.schedule(() => {
        void queryClient.prefetchQuery(showDetailBundleQueryOptions(id));
      });
    },
    [queryClient, router],
  );
  const cancel = React.useCallback(() => intentRef.current.cancel(), []);
  return { prefetch, cancel };
}

export function useMoviePrefetch() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const intentRef = React.useRef(createHoverIntent());
  React.useEffect(() => () => intentRef.current.cancel(), []);
  const prefetch = React.useCallback(
    (movie: Movie) => {
      const id = movie.id;
      if (!id) return;
      router.prefetch(`/dashboard/movies/${id}`);
      intentRef.current.schedule(() => {
        void queryClient.prefetchQuery(movieDetailBundleQueryOptions(id));
      });
    },
    [queryClient, router],
  );
  const cancel = React.useCallback(() => intentRef.current.cancel(), []);
  return { prefetch, cancel };
}

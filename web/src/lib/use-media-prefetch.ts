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

export function useShowPrefetch() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return React.useCallback(
    (show: Show) => {
      if (!show.id) return;
      void queryClient.prefetchQuery(showDetailBundleQueryOptions(show.id));
      router.prefetch(`/dashboard/shows/${show.id}`);
    },
    [queryClient, router],
  );
}

export function useMoviePrefetch() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return React.useCallback(
    (movie: Movie) => {
      if (!movie.id) return;
      void queryClient.prefetchQuery(movieDetailBundleQueryOptions(movie.id));
      router.prefetch(`/dashboard/movies/${movie.id}`);
    },
    [queryClient, router],
  );
}

"use client";

import * as React from "react";
import Link from "next/link";
import { ImageOff, LoaderCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";
import { RequestMediaDialog } from "@/components/request-media-dialog";
import { useUser } from "@/components/providers/user-provider";
import { useFeatures } from "@/components/providers/features-provider";

type SearchResult = components["schemas"]["MetaDataProviderSearchResult"];

export function AddMediaCard({
  result,
  isShow,
  priority = false,
}: {
  result: SearchResult;
  isShow: boolean;
  /** Set on above-the-fold cards (e.g. first row of recommendations). */
  priority?: boolean;
}) {
  const queryClient = useQueryClient();
  const [loading, startLoading] = React.useTransition();
  const [queuedLocal, setQueuedLocal] = React.useState(false);
  const [posterBroken, setPosterBroken] = React.useState(false);
  const [enriched, setEnriched] = React.useState<{
    overview?: string;
    vote_average?: number;
    year?: number;
  } | null>(null);

  React.useEffect(() => {
    if (!isShow && result.imdb_id && result.vote_average == null && !result.overview) {
      fetch(`/api/v1/movies/lookup/${result.imdb_id}`)
        .then((r) => r.json())
        .then((data) => setEnriched(data))
        .catch(() => {});
    }
  }, [isShow, result]);

  const voteAverage = enriched?.vote_average ?? result.vote_average;
  const year = enriched?.year ?? result.year;

  const { user } = useUser();
  const canAdd = !!user?.is_superuser;
  const { requests: requestsEnabled } = useFeatures();

  function addMedia() {
    // Backend returns the existing record when the item is already tracked
    // (response includes `id`) or a `{status: "queued", external_id}` ack
    // when it kicks off a background add. Branch on the shape.
    startLoading(async () => {
      let data: unknown;
      if (isShow) {
        ({ data } = await apiClient.POST("/api/v1/shows", {
          params: {
            query: {
              show_id: result.external_id,
              metadata_provider: result.metadata_provider as "native" | "tmdb" | "tvdb",
              language: result.original_language ?? undefined,
            },
          },
        }));
      } else {
        ({ data } = await apiClient.POST("/api/v1/movies", {
          params: {
            query: {
              movie_id: result.external_id,
              metadata_provider: result.metadata_provider as "native" | "tmdb" | "tvdb",
              language: result.original_language ?? undefined,
            },
          },
        }));
      }
      // Scope to lists this add affects — avoid nuking unrelated caches
      // (settings, users, etc.) on every card click. The add-page grid is
      // keyed on ["media-browse"|"media-search", mediaType]; invalidate those
      // so the just-added card re-renders as "View" (server recomputes the
      // `added`/`id` flags), not just the library list pages.
      const mediaKey = isShow ? "show" : "movie";
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: [isShow ? "shows" : "movies"] }),
        queryClient.invalidateQueries({ queryKey: ["media-browse", mediaKey] }),
        queryClient.invalidateQueries({ queryKey: ["media-search", mediaKey] }),
      ]);
      const id = (data as { id?: string } | undefined)?.id;
      const queued = (data as { status?: string } | undefined)?.status === "queued";
      if (id) {
        toast.success(`"${result.name}" is already in your library`);
      } else if (queued) {
        toast.success(`Adding "${result.name}"`, {
          description: `${isShow ? "Show" : "Movie"} will appear in the library once metadata fetch completes.`,
        });
        setQueuedLocal(true);
      }
    });
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {result.poster_path != null && !posterBroken ? (
        <div className="aspect-[2/3] overflow-hidden rounded-lg">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className="h-full w-full object-cover"
            src={result.poster_path}
            alt={`${result.name}'s Poster Image`}
            onError={() => setPosterBroken(true)}
            width={200}
            height={300}
            style={{ aspectRatio: "2 / 3" }}
            loading={priority ? "eager" : "lazy"}
            decoding="async"
            fetchPriority={priority ? "high" : "auto"}
          />
        </div>
      ) : (
        <div className="flex aspect-[2/3] w-full items-center justify-center rounded-lg bg-muted">
          <ImageOff className="h-12 w-12 text-muted-foreground" />
        </div>
      )}
      <div className="mt-auto flex flex-col gap-2 py-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {year != null && <span>{year}</span>}
          {voteAverage != null && (
            <span className="ml-auto flex items-center font-medium text-yellow-600">
              <svg
                className="mr-1 h-3.5 w-3.5 text-yellow-400"
                fill="currentColor"
                viewBox="0 0 20 20"
              >
                <path d="M10 15l-5.878 3.09 1.122-6.545L.488 6.91l6.561-.955L10 0l2.951 5.955 6.561.955-4.756 4.635 1.122 6.545z" />
              </svg>
              {Math.round(voteAverage)}/10
            </span>
          )}
        </div>
        <p className="line-clamp-2 min-h-[2.5rem] text-sm leading-tight font-medium">
          {result.name}
        </p>
        {result.added ? (
          <Button
            render={
              <Link
                href={
                  isShow
                    ? `/dashboard/shows/${result.id ?? ""}`
                    : `/dashboard/movies/${result.id ?? ""}`
                }
              />
            }
            variant="outline"
            className="w-full text-sm font-semibold"
          >
            View
          </Button>
        ) : (
          <div className="flex gap-2">
            {canAdd && (
              <Button
                variant={queuedLocal ? "secondary" : "default"}
                className={`${requestsEnabled && !canAdd ? "flex-1" : "w-full"} ${queuedLocal ? "text-muted-foreground" : "border-primary"} bg-clip-border font-semibold`}
                disabled={loading || queuedLocal}
                onClick={addMedia}
              >
                {loading ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : queuedLocal ? (
                  "Queued"
                ) : (
                  "Add"
                )}
              </Button>
            )}
            {requestsEnabled && !canAdd && (
              <RequestMediaDialog
                mediaType={isShow ? "show" : "movie"}
                title={result.name + (year != null ? ` (${year})` : "")}
                externalId={String(result.external_id)}
                imdbId={result.imdb_id ?? undefined}
                metadataProvider={result.metadata_provider}
                variant="secondary"
                buttonText="Request"
                className={`${canAdd ? "flex-1" : "w-full"} border-secondary bg-clip-border font-semibold`}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

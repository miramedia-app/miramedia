"use client";

import * as React from "react";
import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Captions, CaptionsOff, Loader2 } from "lucide-react";
import apiClient from "@/lib/api/client";
import { pLimit } from "@/lib/p-limit";
import type { components } from "@/lib/api/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

type SubtitleStatus = components["schemas"]["SubtitleStatus"];
type EpisodeSubtitleStatus = components["schemas"]["EpisodeSubtitleStatus"];

type EpisodeMode = {
  mode: "episode";
  episodeId: string;
  label: string;
  hasSubtitles: boolean;
  onUpdate?: () => void;
};

type ShowMode = {
  mode: "show";
  showId: string;
  showName: string;
  /** When set, scopes the dialog to a single season. */
  seasonNumber?: number;
  hasAllSubtitles: boolean;
  onUpdate?: () => void;
};

type MovieMode = {
  mode: "movie";
  movieId: string;
  label: string;
  hasSubtitles: boolean;
  onUpdate?: () => void;
};

/** When set, the trigger renders as a labelled button instead of an icon. */
type TriggerOpts = { triggerLabel?: string };

type Props = (EpisodeMode | ShowMode | MovieMode) & TriggerOpts;

type SearchResult = { downloaded: string[]; count: number };

async function loadEpisodeStatus(
  episodeId: string,
  signal?: AbortSignal,
): Promise<SubtitleStatus | null> {
  const { data, error } = await apiClient.GET("/api/v1/subtitles/episodes/{episode_id}/status", {
    params: { path: { episode_id: episodeId } },
    signal,
  });
  if (error) throw error;
  return data ?? null;
}

async function loadMovieStatus(
  movieId: string,
  signal?: AbortSignal,
): Promise<SubtitleStatus | null> {
  const { data, error } = await apiClient.GET("/api/v1/subtitles/movies/{movie_id}/status", {
    params: { path: { movie_id: movieId } },
    signal,
  });
  if (error) throw error;
  return data ?? null;
}

async function loadShowStatus(
  showId: string,
  seasonNumber?: number,
  signal?: AbortSignal,
): Promise<EpisodeSubtitleStatus[]> {
  const { data, error } = await apiClient.GET("/api/v1/subtitles/shows/{show_id}/status", {
    params: {
      path: { show_id: showId },
      query: seasonNumber !== undefined ? { season_number: seasonNumber } : {},
    },
    signal,
  });
  if (error) throw error;
  return data?.episodes ?? [];
}

async function searchEpisode(episodeId: string): Promise<SearchResult | null> {
  const { data, response } = await apiClient.POST(
    "/api/v1/subtitles/episodes/{episode_id}/search",
    {
      params: { path: { episode_id: episodeId } },
    },
  );
  if (!response.ok) return null;
  return (data ?? null) as SearchResult | null;
}

async function searchMovie(movieId: string): Promise<SearchResult | null> {
  const { data, response } = await apiClient.POST("/api/v1/subtitles/movies/{movie_id}/search", {
    params: { path: { movie_id: movieId } },
  });
  if (!response.ok) return null;
  return (data ?? null) as SearchResult | null;
}

// Concurrency cap for season-wide subtitle searches. Backend providers can
// rate-limit, and the search hits external HTTP regardless. 5 in flight
// keeps things snappy without overwhelming.
const SEARCH_CONCURRENCY = 5;
const EMPTY_EPISODES: EpisodeSubtitleStatus[] = [];

export function SubtitleSearchDialog(props: Props) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [isSearching, setIsSearching] = React.useState(false);
  const [seasonProgress, setSeasonProgress] = React.useState("");

  const iconHasSubs = props.mode === "show" ? props.hasAllSubtitles : props.hasSubtitles;

  // React Query handles dedup/caching/refetch. Each mode has its own key so
  // reopening the dialog reuses cached status until invalidated.
  const itemQuery = useQuery({
    queryKey:
      props.mode === "episode"
        ? ["subtitles", "episode", props.episodeId]
        : props.mode === "movie"
          ? ["subtitles", "movie", props.movieId]
          : ["subtitles", "noop"],
    queryFn: async ({ signal }) => {
      if (props.mode === "episode") return await loadEpisodeStatus(props.episodeId, signal);
      if (props.mode === "movie") return await loadMovieStatus(props.movieId, signal);
      return null;
    },
    enabled: open && props.mode !== "show",
    staleTime: 30 * 1000,
  });

  const showQuery = useQuery({
    queryKey:
      props.mode === "show"
        ? ["subtitles", "show", props.showId, props.seasonNumber ?? "all"]
        : ["subtitles", "noop"],
    queryFn: async ({ signal }) => {
      if (props.mode !== "show") return [];
      return await loadShowStatus(props.showId, props.seasonNumber, signal);
    },
    enabled: open && props.mode === "show",
    staleTime: 30 * 1000,
  });

  const isLoadingStatus = props.mode === "show" ? showQuery.isLoading : itemQuery.isLoading;
  const loadError = (props.mode === "show" ? showQuery.isError : itemQuery.isError)
    ? "Failed to load subtitle status"
    : null;
  const status = itemQuery.data ?? null;
  // Stable empty-array fallback so dependent useMemos don't see fresh
  // identity per render while the query is loading.
  const showEpisodes = showQuery.data ?? EMPTY_EPISODES;

  function notifyUpdate() {
    props.onUpdate?.();
    void queryClient.invalidateQueries({ queryKey: ["subtitles"] });
  }

  async function handleSearch() {
    setIsSearching(true);
    try {
      let result: SearchResult | null = null;
      if (props.mode === "episode") {
        result = await searchEpisode(props.episodeId);
      } else if (props.mode === "movie") {
        result = await searchMovie(props.movieId);
      }
      if (!result) {
        toast.error("Subtitle search request failed.");
        return;
      }
      if (result.count > 0) {
        toast.success(
          `Downloaded subtitles: ${result.downloaded.map((l) => l.toUpperCase()).join(", ")}`,
        );
      } else {
        toast.info("No subtitles found. Check system logs for details.");
      }
      await itemQuery.refetch();
      notifyUpdate();
    } catch {
      toast.error("Subtitle search failed.");
    } finally {
      setIsSearching(false);
    }
  }

  async function handleSearchAllMissing() {
    if (props.mode !== "show") return;
    setIsSearching(true);
    try {
      const missing = showEpisodes.filter(
        (ep) => ep.downloaded && ep.status.missing_languages.length > 0,
      );
      if (missing.length === 0) {
        toast.info("All downloaded episodes already have subtitles.");
        return;
      }
      let completed = 0;
      let foundCount = 0;
      // Bounded concurrency — many providers + many episodes would otherwise
      // fan out hundreds of requests at once.
      await pLimit(SEARCH_CONCURRENCY, missing, async (ep) => {
        setSeasonProgress(`Searching ${completed + 1}/${missing.length} episodes...`);
        try {
          const result = await searchEpisode(ep.episode_id);
          if (result && result.count > 0) foundCount++;
        } catch {
          /* continue */
        }
        completed++;
      });
      setSeasonProgress("Refreshing status...");
      await showQuery.refetch();
      setSeasonProgress("");
      if (foundCount > 0) {
        toast.success(`Downloaded subtitles for ${foundCount}/${missing.length} episodes.`);
      } else {
        toast.info(
          `No subtitles found for any of the ${missing.length} episodes. Check system logs for details.`,
        );
      }
      notifyUpdate();
    } catch {
      toast.error("Subtitle search failed.");
    } finally {
      setSeasonProgress("");
      setIsSearching(false);
    }
  }

  function handleOpenChange(val: boolean) {
    setOpen(val);
  }

  const scopedToSeason = props.mode === "show" && props.seasonNumber !== undefined;
  const downloadedEpisodes = React.useMemo(
    () =>
      props.mode === "show"
        ? [...showEpisodes]
            .filter((ep) => ep.downloaded)
            .sort(
              (a, b) => a.season_number - b.season_number || a.episode_number - b.episode_number,
            )
        : [],
    [props.mode, showEpisodes],
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {props.triggerLabel ? (
        <DialogTrigger render={<Button variant="outline" size="sm" />}>
          <Captions className="h-4 w-4" />
          {props.triggerLabel}
        </DialogTrigger>
      ) : (
        <DialogTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground"
              title="Subtitles"
            />
          }
        >
          {iconHasSubs ? (
            <Captions className="h-3.5 w-3.5" />
          ) : (
            <CaptionsOff className="h-3.5 w-3.5" />
          )}
        </DialogTrigger>
      )}
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {props.mode === "show"
              ? props.seasonNumber !== undefined
                ? `Subtitles — Season ${props.seasonNumber}`
                : `Subtitles — ${props.showName}`
              : `Subtitles — ${props.label}`}
          </DialogTitle>
          <DialogDescription>
            {props.mode === "show"
              ? "Manage subtitles for all downloaded episodes."
              : "View subtitle status and search for missing subtitles."}
          </DialogDescription>
        </DialogHeader>

        {isLoadingStatus ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : loadError ? (
          <p className="text-sm text-muted-foreground">{loadError}</p>
        ) : props.mode !== "show" ? (
          status ? (
            <>
              <div className="flex flex-col gap-4">
                {status.available_languages.length > 0 && (
                  <div>
                    <p className="mb-2 text-sm font-medium">Available</p>
                    <div className="flex flex-wrap gap-1.5">
                      {status.available_languages.map((lang) => (
                        <Badge key={lang} variant="secondary">
                          {lang.toUpperCase()}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {status.missing_languages.length > 0 && (
                  <div>
                    <p className="mb-2 text-sm font-medium">Missing</p>
                    <div className="flex flex-wrap gap-1.5">
                      {status.missing_languages.map((lang) => (
                        <Badge key={lang} variant="outline">
                          {lang.toUpperCase()}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {status.desired_languages.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    No desired subtitle languages configured. Configure them in system settings.
                  </p>
                )}
              </div>
              <div className="mt-4">
                <Button onClick={handleSearch} disabled={isSearching}>
                  {isSearching ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Searching...
                    </>
                  ) : (
                    "Search & Download"
                  )}
                </Button>
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Could not load subtitle status.</p>
          )
        ) : downloadedEpisodes.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {scopedToSeason
              ? "No downloaded episodes in this season."
              : "No downloaded episodes for this show."}
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              {downloadedEpisodes.map((ep) => {
                const s = ep.status;
                const ok = s.missing_languages.length === 0 && s.available_languages.length > 0;
                const epLabel = scopedToSeason
                  ? `E${String(ep.episode_number).padStart(2, "0")}`
                  : `S${String(ep.season_number).padStart(2, "0")}E${String(ep.episode_number).padStart(2, "0")}`;
                return (
                  <div
                    key={ep.episode_id}
                    className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-muted/30"
                  >
                    <span className="w-14 shrink-0 font-mono text-xs text-muted-foreground">
                      {epLabel}
                    </span>
                    <span className="flex-1 truncate text-sm">{ep.title}</span>
                    {ok ? (
                      <Captions className="h-4 w-4 text-green-500" />
                    ) : (
                      <CaptionsOff className="h-4 w-4 text-muted-foreground" />
                    )}
                    <div className="flex flex-wrap gap-1">
                      {s.available_languages.map((lang) => (
                        <Badge key={lang} variant="secondary" className="text-xs">
                          {lang.toUpperCase()}
                        </Badge>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-4 flex items-center gap-3">
              <Button onClick={handleSearchAllMissing} disabled={isSearching}>
                {isSearching ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {seasonProgress || "Searching..."}
                  </>
                ) : (
                  "Search All Missing"
                )}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

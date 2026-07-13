"use client";

import * as React from "react";
import { LoaderCircle, Search as SearchIcon } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { useQualityCodecOptions } from "@/hooks/use-quality-codec-options";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MetaPill } from "@/components/ui/type-pill";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Checkbox } from "@/components/ui/checkbox";
import apiClient from "@/lib/api/client";
import { createManagedEventSource, type ManagedEventSource } from "@/lib/managed-event-source";
import { getTorrentQualityString } from "@/lib/utils";
import type { components } from "@/lib/api/api";

type Media =
  | components["schemas"]["Show"]
  | components["schemas"]["Movie"]
  | components["schemas"]["PublicShow"]
  | components["schemas"]["PublicMovie"];
type SearchHit = components["schemas"]["IndexerQueryResult"];

const EPISODE_RELEASE_PATTERNS = [
  /s\d{1,2}e\d{1,2}/i,
  /\d{1,2}x\d{1,2}/i,
  /\be\d{1,2}\b/i,
  /e\d{1,2}-e?\d{1,2}/i,
  /vol\.?\s?\d+/i,
];

function FilterChips({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  if (options.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => {
          const active = selected.includes(opt);
          return (
            <Button
              key={opt}
              type="button"
              size="sm"
              variant={active ? "default" : "outline"}
              className="h-7"
              onClick={() => onToggle(opt)}
            >
              {opt}
            </Button>
          );
        })}
      </div>
    </div>
  );
}

export function DownloadMediaDialog({
  open,
  onOpenChange,
  mediaType,
  media,
  seasonNumber: initialSeasonNumber,
  episodeNumber,
  selectedSeasons,
  selectedEpisodes,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mediaType: "show" | "movie";
  media: Media;
  seasonNumber?: number;
  episodeNumber?: number;
  selectedSeasons?: number[];
  selectedEpisodes?: { seasonNumber: number; episodeNumber: number }[];
}) {
  const queryClient = useQueryClient();
  const isShow = mediaType === "show";
  const isMultiSeason = !!selectedSeasons && selectedSeasons.length > 0;
  const isMultiEpisode = !!selectedEpisodes && selectedEpisodes.length > 0;
  const isSingleEpisode = isShow && episodeNumber != null && !isMultiSeason && !isMultiEpisode;
  const episodeLabel =
    isSingleEpisode && initialSeasonNumber != null
      ? `S${String(initialSeasonNumber).padStart(2, "0")}E${String(episodeNumber).padStart(2, "0")}`
      : "";

  const [seasonNumber, setSeasonNumber] = React.useState(initialSeasonNumber ?? 1);
  const [useCustomQuery, setUseCustomQuery] = React.useState(false);
  const [queryOverride, setQueryOverride] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(false);
  const [results, setResults] = React.useState<SearchHit[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  // Search filters (narrow what the indexer returns). Quality of the saved
  // file is auto-detected on download — not chosen here.
  const [searchQualities, setSearchQualities] = React.useState<string[]>([]);
  const [searchCodecs, setSearchCodecs] = React.useState<string[]>([]);

  const advancedMode = useCustomQuery;

  const optionsQuery = useQualityCodecOptions();
  const qualityOptionNames = React.useMemo(
    () =>
      (optionsQuery.data?.qualityOptions ?? [])
        .filter((o) => o.enabled !== false)
        .map((o) => o.name),
    [optionsQuery.data?.qualityOptions],
  );
  const codecOptionNames = React.useMemo(
    () =>
      (optionsQuery.data?.codecOptions ?? []).filter((o) => o.enabled !== false).map((o) => o.name),
    [optionsQuery.data?.codecOptions],
  );

  function toggleIn(list: string[], value: string): string[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  function isEpisodeRelease(title: string) {
    return EPISODE_RELEASE_PATTERNS.some((rx) => rx.test(title.toLowerCase()));
  }

  function matchesEpisode(
    t: SearchHit,
    seasonNumber: number | undefined,
    episodeNumber: number,
  ): boolean {
    // Keep when the result's episode list explicitly includes the wanted
    // episode (single-episode release), or when it has no episode info but
    // covers the right season (season pack containing the episode).
    const eps = (t.episode ?? []) as number[];
    if (eps.length > 0) {
      return eps.includes(episodeNumber);
    }
    const seasons = (t.season ?? []) as number[];
    if (seasonNumber == null) {
      return seasons.length === 0;
    }
    return seasons.length === 0 || seasons.includes(seasonNumber);
  }

  const qualityParam = searchQualities.length > 0 ? searchQualities : undefined;
  const codecParam = searchCodecs.length > 0 ? searchCodecs : undefined;

  // Cancel handle for any in-flight SSE streams so navigating away or
  // re-triggering search aborts the prior streams cleanly.
  const streamControllersRef = React.useRef<ManagedEventSource[]>([]);

  function streamSearch(
    queryParams: Record<string, string | number | undefined>,
    onChunk: (chunk: components["schemas"]["SearchStreamChunk"]) => void,
  ): Promise<void> {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
    const url = new URL(`${apiBase}/api/v1/torrents/search/stream`, window.location.origin);
    for (const [k, v] of Object.entries(queryParams)) {
      if (v != null) url.searchParams.set(k, String(v));
    }
    if (qualityParam) {
      for (const q of qualityParam) url.searchParams.append("quality", q);
    }
    if (codecParam) {
      for (const c of codecParam) url.searchParams.append("codec", c);
    }
    return new Promise<void>((resolve) => {
      // EventSource fires onerror on TRANSIENT reconnects too (readyState
      // CONNECTING), not only terminal failures. The primitive finishes
      // immediately only when the browser has terminally given up (CLOSED);
      // otherwise it lets the stream auto-reconnect and waits for `done`,
      // capping the wait (10s) so a truly-down server can't hang the search.
      const handle: ManagedEventSource = createManagedEventSource(url.toString(), {
        withCredentials: true,
        timeoutMs: 10000,
        doneEvent: "done",
        events: {
          results: (ev) => {
            try {
              onChunk(JSON.parse(ev.data) as components["schemas"]["SearchStreamChunk"]);
            } catch (err) {
              console.error("SSE parse error", err);
            }
          },
        },
        // Every terminal outcome (completed / closed / timeout) resolves the
        // promise, exactly as the prior single `finish()` did.
        onDone: () => {
          streamControllersRef.current = streamControllersRef.current.filter((x) => x !== handle);
          resolve();
        },
      });
      streamControllersRef.current.push(handle);
    });
  }

  function dedupAndFilter(items: SearchHit[], extra?: (t: SearchHit) => boolean): SearchHit[] {
    // Backend assigns a fresh UUID to every IndexerQueryResult on each
    // fetch, so dedup-by-id is a no-op. Use a stable fingerprint built
    // from the title + size (size is the strongest stable signal across
    // sites for the same release). Falls back to title when size is 0.
    const seen = new Set<string>();
    const out: SearchHit[] = [];
    for (const t of items) {
      if (extra && !extra(t)) continue;
      const key = `${t.title}|${t.size ?? 0}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(t);
    }
    // Score first — scoring rulesets already incorporate quality. Then
    // usenet preference, then seeders (torrents) or age (usenet), then
    // smaller size. Stable on full ties via title.
    out.sort((a, b) => {
      const ds = (b.score ?? 0) - (a.score ?? 0);
      if (ds !== 0) return ds;
      if (a.usenet !== b.usenet) return a.usenet ? -1 : 1;
      if (a.usenet && b.usenet) {
        return (b.age ?? 0) - (a.age ?? 0);
      }
      const aSeed = a.seeders ?? 0;
      const bSeed = b.seeders ?? 0;
      if (aSeed !== bSeed) return bSeed - aSeed;
      const aSize = a.size ?? 0;
      const bSize = b.size ?? 0;
      if (aSize !== bSize) return aSize - bSize;
      return a.title.localeCompare(b.title);
    });
    return out;
  }

  async function search() {
    // Abort any in-flight streams from a prior search.
    for (const es of streamControllersRef.current) es.close();
    streamControllersRef.current = [];

    setIsLoading(true);
    setError(null);
    setResults([]);

    const allResults: SearchHit[] = [];
    const pushChunk = (
      chunk: components["schemas"]["SearchStreamChunk"],
      extra?: (t: SearchHit) => boolean,
    ) => {
      allResults.push(...chunk.results);
      setResults(dedupAndFilter([...allResults], extra));
    };

    try {
      if (isMultiSeason && selectedSeasons) {
        toast.info(`Searching torrents for seasons: ${selectedSeasons.join(", ")}`);
        await Promise.all(
          selectedSeasons.map((sn) =>
            streamSearch(
              {
                media_type: "show",
                media_id: media.id!,
                season_number: sn,
              },
              (chunk) => pushChunk(chunk, (t) => !isEpisodeRelease(t.title)),
            ),
          ),
        );
      } else if (isMultiEpisode && selectedEpisodes) {
        toast.info(`Searching torrents for ${selectedEpisodes.length} episodes...`);
        await Promise.all(
          selectedEpisodes.map((ep) =>
            streamSearch(
              {
                media_type: "show",
                media_id: media.id!,
                season_number: ep.seasonNumber,
                episode_number: ep.episodeNumber,
              },
              (chunk) => pushChunk(chunk),
            ),
          ),
        );
      } else {
        // When the user picked a specific episode, the backend search
        // pulls the full season (so EZTV's IMDb-ID filter works) and we
        // narrow client-side to the requested episode + matching season
        // packs. Without this filter we leak other episodes from the
        // season into the dialog.
        const targetSeason = isShow && !advancedMode ? seasonNumber : undefined;
        const targetEpisode = isShow ? episodeNumber : undefined;
        const filterForEpisode =
          isShow && !advancedMode && targetEpisode != null
            ? (t: SearchHit) => matchesEpisode(t, targetSeason, targetEpisode)
            : undefined;
        await streamSearch(
          {
            media_type: mediaType,
            media_id: media.id!,
            season_number: targetSeason,
            episode_number: targetEpisode,
            query_override: advancedMode ? queryOverride : undefined,
          },
          (chunk) => pushChunk(chunk, filterForEpisode),
        );
      }
      toast.info(`Found ${allResults.length} torrents.`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Search failed";
      setError(msg);
      toast.error(msg);
    } finally {
      setIsLoading(false);
    }
  }

  // Close any open EventSources on unmount.
  React.useEffect(() => {
    return () => {
      for (const es of streamControllersRef.current) es.close();
      streamControllersRef.current = [];
    };
  }, []);

  async function downloadTorrent(resultId: string) {
    // Free the SSE connection slot before issuing the POST. Chrome's
    // per-origin HTTP/1.1 connection cap (~6) can starve the POST while a
    // long-running search stream still holds a slot, surfacing as
    // ``TypeError: Failed to fetch`` in apiClient.onError.
    for (const es of streamControllersRef.current) es.close();
    streamControllersRef.current = [];
    setError(null);
    const { response } = await apiClient.POST("/api/v1/torrents/download", {
      body: {
        indexer_result_id: resultId,
        media_type: mediaType,
        media_id: media.id!,
        variant: "",
        quality_override: null,
        library: null,
      },
    });
    if (response.status === 409) {
      const msg = "A file already exists at that quality + variant. Pick a different variant tag.";
      setError(msg);
      toast.info(msg);
    } else if (!response.ok) {
      const msg = `Failed to download torrent: ${response.statusText}`;
      setError(msg);
      toast.error(msg);
    } else {
      toast.success("Torrent download started successfully!");
      onOpenChange(false);
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["torrents"] }),
      queryClient.invalidateQueries({ queryKey: [isShow ? "show" : "movie", media.id] }),
    ]);
  }

  const dialogTitle = isShow
    ? isMultiSeason
      ? "Download Selected Seasons"
      : isMultiEpisode
        ? "Download Selected Episodes"
        : isSingleEpisode
          ? `Download Episode ${episodeLabel}`
          : "Download a Season"
    : "Download a Movie";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[95vw] max-w-6xl sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>{dialogTitle}</DialogTitle>
          <DialogDescription>
            {isSingleEpisode
              ? "Search and download a torrent for this episode. Season packs containing it are also matched."
              : isShow
                ? "Search and download torrents for a specific season or season packs."
                : "Search and download torrents for this movie."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-4 rounded-lg border bg-muted/20 p-4">
            {/* Scope */}
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Searching for
              </span>
              {isMultiSeason && selectedSeasons ? (
                <p className="text-sm">
                  Seasons{" "}
                  <strong>
                    {selectedSeasons
                      .toSorted((a, b) => a - b)
                      .map((n) => `S${String(n).padStart(2, "0")}`)
                      .join(", ")}
                  </strong>
                </p>
              ) : isMultiEpisode && selectedEpisodes ? (
                <p className="text-sm">
                  Episodes{" "}
                  <strong>
                    {selectedEpisodes
                      .map(
                        (e) =>
                          `S${String(e.seasonNumber).padStart(2, "0")}E${String(e.episodeNumber).padStart(2, "0")}`,
                      )
                      .join(", ")}
                  </strong>
                </p>
              ) : isSingleEpisode ? (
                <p className="text-sm">
                  Episode <strong>{episodeLabel}</strong>
                </p>
              ) : isShow ? (
                <div className="flex items-center gap-2">
                  <Label htmlFor="season-number" className="text-sm">
                    Season
                  </Label>
                  <Input
                    type="number"
                    id="season-number"
                    className="w-20"
                    value={seasonNumber}
                    disabled={advancedMode}
                    onChange={(e) => setSeasonNumber(Number(e.target.value) || 1)}
                  />
                </div>
              ) : (
                <p className="text-sm">
                  <strong>{media.name}</strong>
                </p>
              )}
            </div>

            {/* Filters */}
            {(qualityOptionNames.length > 0 || codecOptionNames.length > 0) && (
              <div className="grid gap-4 sm:grid-cols-2">
                <FilterChips
                  label="Quality"
                  options={qualityOptionNames}
                  selected={searchQualities}
                  onToggle={(v) => setSearchQualities((s) => toggleIn(s, v))}
                />
                <FilterChips
                  label="Codec"
                  options={codecOptionNames}
                  selected={searchCodecs}
                  onToggle={(v) => setSearchCodecs((s) => toggleIn(s, v))}
                />
              </div>
            )}

            {/* Custom query */}
            {!isMultiSeason && !isMultiEpisode && (
              <div className="flex flex-col gap-2">
                <label className="flex w-fit items-center gap-2 text-sm">
                  <Checkbox
                    checked={useCustomQuery}
                    onCheckedChange={(v) => setUseCustomQuery(v === true)}
                  />
                  Custom search query
                </label>
                {useCustomQuery && (
                  <Input
                    value={queryOverride}
                    onChange={(e) => setQueryOverride(e.target.value)}
                    placeholder="e.g. Show Name S01 2160p REMUX"
                    className="max-w-md"
                  />
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={search} disabled={isLoading}>
                {isLoading ? (
                  <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <SearchIcon className="mr-2 h-4 w-4" />
                )}
                {isLoading ? "Searching…" : "Search"}
              </Button>
              {(searchQualities.length > 0 || searchCodecs.length > 0) && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setSearchQualities([]);
                    setSearchCodecs([]);
                  }}
                >
                  Clear filters
                </Button>
              )}
              {results && (
                <span className="text-xs text-muted-foreground">
                  {results.length} result{results.length !== 1 ? "s" : ""}
                </span>
              )}
            </div>
          </div>
        </div>

        {error && <div className="my-2 w-full text-center text-sm text-red-500">{error}</div>}

        {results && (
          <div className="max-h-[55vh] overflow-y-auto">
            <Table className="w-full table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[330px] pr-6">Title</TableHead>
                  <TableHead className="w-[70px]">Quality</TableHead>
                  <TableHead className="w-[70px]">Size</TableHead>
                  <TableHead className="w-[60px]">Seed</TableHead>
                  <TableHead className="w-[60px]">Score</TableHead>
                  <TableHead className="w-[100px]">Indexer</TableHead>
                  {isShow && <TableHead className="w-[40px]">Season</TableHead>}
                  <TableHead className="w-[110px] text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {results.map((t) => (
                  <TableRow key={t.id ?? t.title}>
                    <TableCell className="pr-6 font-medium break-words whitespace-normal">
                      {t.title}
                    </TableCell>
                    <TableCell>
                      <MetaPill className="font-mono">
                        {getTorrentQualityString(t.quality)}
                      </MetaPill>
                    </TableCell>
                    <TableCell>{(t.size / 1024 / 1024 / 1024).toFixed(2)}GB</TableCell>
                    <TableCell>{t.usenet ? "N/A" : t.seeders == null ? "—" : t.seeders}</TableCell>
                    <TableCell>{t.score}</TableCell>
                    <TableCell className="break-words whitespace-normal">
                      {t.indexer ?? "unknown"}
                    </TableCell>
                    {isShow && (
                      <TableCell>
                        {t.season && t.season.length > 0 ? (
                          <MetaPill className="font-mono">
                            S{t.season.map((s) => String(s).padStart(2, "0")).join(", ")}
                          </MetaPill>
                        ) : null}
                      </TableCell>
                    )}
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        disabled={!t.id}
                        onClick={() => t.id && downloadTorrent(t.id)}
                      >
                        Download
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {results.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={isShow ? 8 : 7}
                      className="text-center text-muted-foreground"
                    >
                      {isLoading ? "Searching…" : "No torrents found."}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        )}

        {!results && !isLoading && (
          <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
            Set any filters above, then run a search.
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function SearchTorrentButton({
  show,
  movie,
  seasonNumber,
  episodeNumber,
  label = "Search",
  size = "sm",
  iconOnly = false,
}: {
  show?: components["schemas"]["PublicShow"];
  movie?: components["schemas"]["PublicMovie"];
  seasonNumber?: number;
  episodeNumber?: number;
  label?: string;
  size?: "sm" | "default" | "icon" | "lg";
  iconOnly?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const mediaType: "show" | "movie" = show ? "show" : "movie";
  const media = (show ?? movie)!;
  return (
    <>
      <Button
        variant="ghost"
        size={iconOnly ? "icon" : size}
        className={iconOnly ? "h-7 w-7 text-muted-foreground" : "h-7"}
        title={iconOnly ? label : undefined}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        {iconOnly ? <SearchIcon className="h-3.5 w-3.5" /> : label}
      </Button>
      {open && (
        <DownloadMediaDialog
          open={open}
          onOpenChange={setOpen}
          mediaType={mediaType}
          media={media}
          seasonNumber={seasonNumber}
          episodeNumber={episodeNumber}
        />
      )}
    </>
  );
}

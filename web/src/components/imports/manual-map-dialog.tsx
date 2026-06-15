"use client";

import * as React from "react";
import { toast } from "sonner";
import { LoaderCircle, FileVideo, FileText } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type ManualMapItem = components["schemas"]["ManualMapItem"];

type Row = {
  relative_path: string;
  size: number;
  is_video: boolean;
  is_subtitle: boolean;
  seasons: number[];
  episodes: number[];
  quality: string;
  target: string; // 'skip' | `episode:<id>` | `movie:<id>`
  variant: string;
};

type EpisodeOption = { id: string; label: string; season: number; episode: number };

function formatSize(bytes: number): string {
  if (bytes <= 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let val = bytes;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(1)} ${units[i]}`;
}

export function ManualMapDialog({
  torrentId,
  torrentTitle,
  open,
  onOpenChange,
  onApplied,
}: {
  torrentId: string;
  torrentTitle: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied?: () => void;
}) {
  const [isLoading, setIsLoading] = React.useState(false);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [rows, setRows] = React.useState<Row[]>([]);
  const [episodeOptions, setEpisodeOptions] = React.useState<EpisodeOption[]>([]);
  const [movieId, setMovieId] = React.useState<string | null>(null);
  const [movieLabel, setMovieLabel] = React.useState("");

  const loadFiles = React.useCallback(async () => {
    setRows([]);
    setEpisodeOptions([]);
    setMovieId(null);
    setMovieLabel("");
    setIsLoading(true);
    try {
      const { data, error } = await apiClient.GET("/api/v1/torrents/{torrent_id}/files", {
        params: { path: { torrent_id: torrentId } },
      });
      if (error || !data) throw new Error("failed");
      const nextRows: Row[] = (data.files ?? []).map((f) => ({
        relative_path: f.relative_path,
        size: f.size ?? 0,
        is_video: !!f.is_video,
        is_subtitle: !!f.is_subtitle,
        seasons: f.seasons ?? [],
        episodes: f.episodes ?? [],
        quality: String(f.quality ?? "unknown"),
        target: f.suggested_episode_id
          ? `episode:${f.suggested_episode_id}`
          : f.suggested_movie_id
            ? `movie:${f.suggested_movie_id}`
            : "skip",
        variant: "",
      }));
      setRows(nextRows);

      if (data.media?.media_type === "show") {
        const { data: show } = await apiClient.GET("/api/v1/shows/{show_id}", {
          params: { path: { show_id: data.media.media_id } },
        });
        const opts: EpisodeOption[] = (show?.seasons ?? [])
          .flatMap((s) =>
            (s.episodes ?? []).map((e) => ({
              id: e.id ?? "",
              season: s.number,
              episode: e.number,
              label: `S${String(s.number).padStart(2, "0")}E${String(e.number).padStart(2, "0")} — ${e.title ?? ""}`,
            })),
          )
          .sort((a, b) => (a.season === b.season ? a.episode - b.episode : a.season - b.season));
        setEpisodeOptions(opts);
        setMovieId(null);
      } else if (data.media?.media_type === "movie") {
        setMovieId(data.media.media_id);
        setMovieLabel(
          `${data.media.media_name}${data.media.media_year ? ` (${data.media.media_year})` : ""}`,
        );
        setEpisodeOptions([]);
      } else {
        setEpisodeOptions([]);
        setMovieId(null);
      }
    } catch {
      toast.error("Failed to load source files.");
    } finally {
      setIsLoading(false);
    }
  }, [torrentId]);

  React.useEffect(() => {
    if (open) void loadFiles();
  }, [open, loadFiles]);

  function updateRow(idx: number, partial: Partial<Row>) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...partial } : r)));
  }

  async function submit() {
    setIsSubmitting(true);
    try {
      const items: ManualMapItem[] = rows.map((r) => {
        if (r.target === "skip") {
          return {
            relative_path: r.relative_path,
            target_type: "skip",
            variant: r.variant,
          };
        }
        const [kind, id] = r.target.split(":");
        if (kind === "episode") {
          return {
            relative_path: r.relative_path,
            target_type: "episode",
            episode_id: id,
            variant: r.variant,
          };
        }
        return {
          relative_path: r.relative_path,
          target_type: "movie",
          movie_id: id,
          variant: r.variant,
        };
      });
      const { data, error } = await apiClient.POST("/api/v1/torrents/{torrent_id}/map", {
        params: { path: { torrent_id: torrentId } },
        body: { items },
      });
      if (error || !data) throw new Error("failed");
      if (data.failed > 0) {
        toast.error(`${data.mapped} mapped, ${data.failed} failed.`);
      } else {
        toast.success(`Mapped ${data.mapped} file${data.mapped === 1 ? "" : "s"}.`);
      }
      onOpenChange(false);
      onApplied?.();
    } catch {
      toast.error("Mapping failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  // Per-row preview was doing episodeOptions.find() per render, which is
  // O(rows × episodes) on big shows. Map lookup is O(1).
  const episodeById = React.useMemo(() => {
    const m = new Map<string, EpisodeOption>();
    for (const opt of episodeOptions) m.set(opt.id, opt);
    return m;
  }, [episodeOptions]);

  function previewTarget(row: Row): string {
    if (row.target === "skip") return "skip";
    const [kind, id] = row.target.split(":");
    if (kind === "episode") {
      const opt = episodeById.get(id ?? "");
      return opt?.label ?? "?";
    }
    if (kind === "movie") return movieLabel || "movie";
    return "?";
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] w-fit min-w-[80vw] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Manually map files</DialogTitle>
          <DialogDescription>
            Assign each torrent file ({torrentTitle}) to an episode or movie. Skipped files stay on
            disk untouched.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex justify-center py-10">
            <Spinner className="size-8" />
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No files found in the torrent directory.</p>
        ) : (
          <>
            <div className="rounded-md border">
              <div className="grid grid-cols-[minmax(220px,2fr)_120px_auto_minmax(220px,2fr)_140px] gap-2 border-b bg-muted/50 px-3 py-2 text-xs font-semibold text-muted-foreground">
                <span>File</span>
                <span>Size</span>
                <span>Detected</span>
                <span>Target</span>
                <span>Variant</span>
              </div>
              {rows.map((row, idx) => (
                <div
                  key={row.relative_path}
                  className="grid grid-cols-[minmax(220px,2fr)_120px_auto_minmax(220px,2fr)_140px] gap-2 border-b px-3 py-2 text-xs last:border-b-0"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    {row.is_video ? (
                      <FileVideo className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    ) : row.is_subtitle ? (
                      <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    ) : null}
                    <span className="truncate font-mono" title={row.relative_path}>
                      {row.relative_path}
                    </span>
                  </div>
                  <span className="tabular-nums">{formatSize(row.size)}</span>
                  <div className="flex flex-wrap items-center gap-1">
                    {(row.seasons.length || row.episodes.length) > 0 && (
                      <Badge variant="outline" className="text-[10px]">
                        {row.seasons.map((s) => `S${String(s).padStart(2, "0")}`).join(",")}
                        {row.episodes.map((e) => `E${String(e).padStart(2, "0")}`).join(",")}
                      </Badge>
                    )}
                    {row.quality && row.quality !== "unknown" && (
                      <Badge variant="outline" className="text-[10px]">
                        {row.quality}
                      </Badge>
                    )}
                  </div>
                  <Select value={row.target} onValueChange={(v) => updateRow(idx, { target: v })}>
                    <SelectTrigger className="text-xs">
                      <SelectValue>{previewTarget(row)}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="skip">Skip</SelectItem>
                      {movieId && (
                        <SelectItem value={`movie:${movieId}`}>Movie: {movieLabel}</SelectItem>
                      )}
                      {episodeOptions.map((opt) => (
                        <SelectItem key={opt.id} value={`episode:${opt.id}`}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    type="text"
                    placeholder="(none)"
                    value={row.variant}
                    onChange={(e) => updateRow(idx, { variant: e.target.value })}
                    className="h-8 text-xs"
                  />
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Tip: assigning two files to the same target uses the variant to distinguish them (e.g.{" "}
              <code>director-cut</code>).
            </p>
          </>
        )}

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={isSubmitting || isLoading || rows.length === 0}>
            {isSubmitting && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
            Apply mapping
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

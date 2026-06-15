"use client";

import * as React from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Upload, Link as LinkIcon, LoaderCircle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import apiClient from "@/lib/api/client";
import { getTorrentQualityString, qualityToNumber } from "@/lib/utils";
import type { components } from "@/lib/api/api";
import { MatchConfidencePill } from "@/components/match-confidence-pill";
import { FilePathSuffixSelector } from "@/components/torrents/file-path-suffix-selector";

type ManualParseResponse = components["schemas"]["ManualParseResponse"];
type ManualParseCandidate = components["schemas"]["ManualParseCandidate"];
type SearchResult = components["schemas"]["MetaDataProviderSearchResult"];
type Quality = "uhd" | "fullhd" | "hd" | "sd" | "unknown";

type Step = "input" | "review" | "add-new";

export function AddTorrentDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [step, setStep] = React.useState<Step>("input");
  const [isLoading, setIsLoading] = React.useState(false);
  const [magnetLink, setMagnetLink] = React.useState("");
  const [torrentFile, setTorrentFile] = React.useState<File | null>(null);
  const [parseResult, setParseResult] = React.useState<ManualParseResponse | null>(null);
  const [selectedCandidate, setSelectedCandidate] = React.useState<ManualParseCandidate | null>(
    null,
  );
  const [variant, setVariant] = React.useState("");
  const [quality, setQuality] = React.useState<Quality>("unknown");
  const [downloadError, setDownloadError] = React.useState<string | null>(null);

  const [newMediaType, setNewMediaType] = React.useState<"show" | "movie">("show");
  const [newMediaQuery, setNewMediaQuery] = React.useState("");
  const [newMediaProvider, setNewMediaProvider] = React.useState<"native" | "tmdb" | "tvdb">(
    "native",
  );
  const [newMediaResults, setNewMediaResults] = React.useState<SearchResult[]>([]);
  const [newMediaSearching, setNewMediaSearching] = React.useState(false);
  const [addingExternalId, setAddingExternalId] = React.useState<string | null>(null);

  function reset() {
    setStep("input");
    setIsLoading(false);
    setMagnetLink("");
    setTorrentFile(null);
    setParseResult(null);
    setSelectedCandidate(null);
    setVariant("");
    setQuality("unknown");
    setDownloadError(null);
    setNewMediaQuery("");
    setNewMediaResults([]);
    setAddingExternalId(null);
  }

  async function searchNewMedia(query?: string) {
    const q = (query ?? newMediaQuery).trim();
    if (!q) return;
    setNewMediaSearching(true);
    try {
      if (newMediaType === "show") {
        const { data, error } = await apiClient.GET("/api/v1/shows/search", {
          params: { query: { query: q, metadata_provider: newMediaProvider } },
        });
        if (error) throw new Error("search failed");
        setNewMediaResults(data ?? []);
      } else {
        const { data, error } = await apiClient.GET("/api/v1/movies/search", {
          params: { query: { query: q, metadata_provider: newMediaProvider } },
        });
        if (error) throw new Error("search failed");
        setNewMediaResults(data ?? []);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown error";
      toast.error(`Metadata search failed: ${msg}`);
    } finally {
      setNewMediaSearching(false);
    }
  }

  async function addNewMedia(result: SearchResult) {
    setAddingExternalId(result.external_id);
    try {
      const resp =
        newMediaType === "show"
          ? await apiClient.POST("/api/v1/shows", {
              params: {
                query: {
                  show_id: result.external_id,
                  metadata_provider: newMediaProvider,
                },
              },
            })
          : await apiClient.POST("/api/v1/movies", {
              params: {
                query: {
                  movie_id: result.external_id,
                  metadata_provider: newMediaProvider,
                },
              },
            });
      const { data, error } = resp;
      if (error || !data) throw new Error("create failed");
      const payload = data as Record<string, unknown>;
      if (typeof payload.id !== "string" || typeof payload.name !== "string") {
        toast.info(
          `${newMediaType === "show" ? "Show" : "Movie"} queued. Wait a moment, then re-open this dialog to attach the torrent.`,
        );
        return;
      }
      const created = {
        id: payload.id,
        name: payload.name,
        year: typeof payload.year === "number" ? payload.year : null,
      };
      const candidate: ManualParseCandidate = {
        media_type: newMediaType,
        media_id: created.id,
        media_name: created.name,
        media_year: created.year,
        confidence: 1,
        breakdown: null,
      };
      // Batch the three setState calls so the dialog doesn't re-render
      // twice between candidate selection, parse update, and step change.
      React.startTransition(() => {
        setSelectedCandidate(candidate);
        setParseResult((prev) =>
          prev ? { ...prev, candidates: [candidate, ...prev.candidates] } : prev,
        );
        setStep("review");
      });
      toast.success(`${newMediaType === "show" ? "Show" : "Movie"} "${created.name}" added.`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown";
      toast.error(`Failed to add ${newMediaType}: ${msg}`);
    } finally {
      setAddingExternalId(null);
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    setTorrentFile(e.target.files?.[0] ?? null);
  }

  async function handleParse() {
    if (!magnetLink && !torrentFile) {
      toast.error("Please provide a magnet link or upload a .torrent file.");
      return;
    }
    setIsLoading(true);
    setDownloadError(null);
    try {
      const formData = new FormData();
      if (magnetLink) formData.append("magnet_link", magnetLink);
      if (torrentFile) formData.append("torrent_file", torrentFile);

      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "";
      const response = await fetch(`${baseUrl}/api/v1/torrents/manual/parse`, {
        method: "POST",
        body: formData,
        credentials: "include",
      });

      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.clone().json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          try {
            detail = (await response.text()) || detail;
          } catch {
            /* ignore */
          }
        }
        throw new Error(detail);
      }

      const parsed: ManualParseResponse = await response.json();
      setParseResult(parsed);
      setSelectedCandidate(parsed.candidates[0] ?? null);
      setStep("review");
      toast.success("Torrent parsed successfully!");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown error";
      toast.error(`Failed to parse: ${msg}`);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleDownload() {
    if (!parseResult || !selectedCandidate) {
      toast.error("Please select a media item to link this torrent to.");
      return;
    }
    setIsLoading(true);
    setDownloadError(null);
    const { response } = await apiClient.POST("/api/v1/torrents/manual/download", {
      body: {
        download_token: parseResult.download_token,
        media_type: selectedCandidate.media_type as "show" | "movie",
        media_id: selectedCandidate.media_id,
        variant: variant || "",
        quality_override: quality !== "unknown" ? qualityToNumber(quality) : null,
      },
    });
    if (!response.ok) {
      const errorText = await response.text();
      const message = `Download failed: ${errorText}`;
      setDownloadError(message);
      toast.error(message);
      setIsLoading(false);
      return;
    }
    toast.success("Torrent download started!");
    setIsLoading(false);
    setOpen(false);
    reset();
    await queryClient.invalidateQueries({ queryKey: ["torrents"] });
  }

  // Memoize the synthetic media object so FilePathSuffixSelector and any
  // downstream React.memo can short-circuit when other state changes.
  const stubMediaForSelected = React.useMemo<
    components["schemas"]["Show"] | components["schemas"]["Movie"] | null
  >(() => {
    if (!selectedCandidate) return null;
    const c = selectedCandidate;
    const base = {
      id: c.media_id,
      name: c.media_name,
      year: c.media_year ?? null,
      overview: "",
      external_id: "",
      metadata_provider: "native",
      skipped: false,
      library: "Default",
    };
    if (c.media_type === "show") {
      return {
        ...base,
        ended: false,
        wanted_episode_count: 0,
        downloaded_episode_count: 0,
        list_progress_status: "none",
        seasons: [],
      } satisfies components["schemas"]["Show"];
    }
    return { ...base, downloaded: false } satisfies components["schemas"]["Movie"];
  }, [selectedCandidate]);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger render={<Button variant="default" size="default" className="gap-1 text-xs" />}>
        <Plus className="h-4 w-4" />
        Add Torrent
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] w-fit max-w-[80vw] min-w-[600px] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{step === "input" ? "Add Torrent" : "Review & Download"}</DialogTitle>
          <DialogDescription>
            {step === "input"
              ? "Paste a magnet link or upload a .torrent file to add a torrent manually."
              : "Review the parsed torrent and assign it to a media item."}
          </DialogDescription>
        </DialogHeader>

        {step === "input" && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="magnet-link" className="flex items-center gap-2">
                <LinkIcon size={16} />
                Magnet Link
              </Label>
              <Textarea
                id="magnet-link"
                value={magnetLink}
                onChange={(e) => setMagnetLink(e.target.value)}
                placeholder="magnet:?xt=urn:btih:..."
                rows={3}
                className="font-mono text-sm"
              />
            </div>

            <div className="flex items-center gap-4">
              <div className="h-px flex-1 bg-border" />
              <span className="text-sm text-muted-foreground">OR</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="torrent-file" className="flex items-center gap-2">
                <Upload size={16} />
                .torrent File
              </Label>
              <Input id="torrent-file" type="file" accept=".torrent" onChange={handleFileChange} />
              {torrentFile && (
                <p className="text-sm text-muted-foreground">Selected: {torrentFile.name}</p>
              )}
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleParse} disabled={isLoading || (!magnetLink && !torrentFile)}>
                {isLoading && <LoaderCircle className="mr-2 animate-spin" size={16} />}
                Parse Torrent
              </Button>
            </div>
          </div>
        )}

        {step === "review" && parseResult && (
          <div className="flex flex-col gap-4">
            <div className="rounded-md border p-4">
              <h3 className="mb-2 font-semibold">Parsed Information</h3>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <span className="text-muted-foreground">Title:</span>
                <span className="font-medium">{parseResult.title}</span>
                <span className="text-muted-foreground">Quality:</span>
                <span>
                  <MetaPill className="font-mono">
                    {getTorrentQualityString(parseResult.quality)}
                  </MetaPill>
                </span>
                {parseResult.seasons.length > 0 && (
                  <>
                    <span className="text-muted-foreground">Seasons:</span>
                    <span>
                      {parseResult.seasons.map((s) => `S${String(s).padStart(2, "0")}`).join(", ")}
                    </span>
                  </>
                )}
                {parseResult.episodes.length > 0 && (
                  <>
                    <span className="text-muted-foreground">Episodes:</span>
                    <span>
                      {parseResult.episodes.map((e) => `E${String(e).padStart(2, "0")}`).join(", ")}
                    </span>
                  </>
                )}
              </div>
            </div>

            <div>
              <h3 className="mb-2 font-semibold">Assign to Media</h3>
              {parseResult.candidates.length > 0 ? (
                <div className="max-h-[200px] overflow-y-auto rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[40px]"></TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Name</TableHead>
                        <TableHead>Year</TableHead>
                        <TableHead>Confidence</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {parseResult.candidates.map((c) => (
                        <TableRow
                          key={c.media_id}
                          className={
                            selectedCandidate?.media_id === c.media_id
                              ? "cursor-pointer bg-muted"
                              : "cursor-pointer"
                          }
                          onClick={() => setSelectedCandidate(c)}
                        >
                          <TableCell>
                            <input
                              type="radio"
                              name="candidate"
                              checked={selectedCandidate?.media_id === c.media_id}
                              onChange={() => setSelectedCandidate(c)}
                            />
                          </TableCell>
                          <TableCell>
                            <TypePill>{c.media_type === "show" ? "Show" : "Movie"}</TypePill>
                          </TableCell>
                          <TableCell className="font-medium">{c.media_name}</TableCell>
                          <TableCell>{c.media_year ?? "-"}</TableCell>
                          <TableCell>
                            <MatchConfidencePill
                              confidence={c.confidence}
                              breakdown={c.breakdown}
                            />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No matching media found in your library.
                </p>
              )}

              <Button
                variant="link"
                className="mt-1 self-start px-0"
                onClick={() => {
                  const q = parseResult?.title ?? "";
                  setNewMediaQuery(q);
                  setStep("add-new");
                  if (q) void searchNewMedia(q);
                }}
              >
                + Search for new {parseResult.seasons.length > 0 ? "show" : "movie"}
              </Button>
            </div>

            {selectedCandidate && stubMediaForSelected && (
              <FilePathSuffixSelector
                quality={quality}
                onQualityChange={setQuality}
                variant={variant}
                onVariantChange={setVariant}
                media={stubMediaForSelected}
                mediaType={selectedCandidate.media_type as "show" | "movie"}
              />
            )}

            {downloadError && (
              <div className="text-center text-sm text-red-500">{downloadError}</div>
            )}

            <div className="flex justify-between gap-2">
              <Button variant="secondary" onClick={() => setStep("input")}>
                Back
              </Button>
              <Button onClick={handleDownload} disabled={isLoading || !selectedCandidate}>
                {isLoading && <LoaderCircle className="mr-2 animate-spin" size={16} />}
                Download Torrent
              </Button>
            </div>
          </div>
        )}

        {step === "add-new" && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <Select
                  value={newMediaType}
                  onValueChange={(v) => setNewMediaType(v as "show" | "movie")}
                >
                  <SelectTrigger className="w-[120px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="show">Show</SelectItem>
                    <SelectItem value="movie">Movie</SelectItem>
                  </SelectContent>
                </Select>
                <Select
                  value={newMediaProvider}
                  onValueChange={(v) => setNewMediaProvider(v as "native" | "tmdb" | "tvdb")}
                >
                  <SelectTrigger className="w-[120px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="native">native</SelectItem>
                    <SelectItem value="tmdb">tmdb</SelectItem>
                    <SelectItem value="tvdb">tvdb</SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  type="text"
                  value={newMediaQuery}
                  onChange={(e) => setNewMediaQuery(e.target.value)}
                  placeholder="Search query"
                  className="flex-1"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void searchNewMedia();
                  }}
                />
                <Button onClick={() => void searchNewMedia()} disabled={newMediaSearching}>
                  {newMediaSearching ? (
                    <LoaderCircle className="animate-spin" size={16} />
                  ) : (
                    "Search"
                  )}
                </Button>
              </div>
            </div>

            {newMediaResults.length > 0 ? (
              <div className="max-h-[300px] overflow-y-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Year</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {newMediaResults.map((r) => (
                      <TableRow key={r.external_id}>
                        <TableCell className="font-medium">{r.name}</TableCell>
                        <TableCell>{r.year ?? "—"}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            disabled={addingExternalId !== null}
                            onClick={() => void addNewMedia(r)}
                          >
                            {addingExternalId === r.external_id ? (
                              <LoaderCircle className="animate-spin" size={14} />
                            ) : (
                              "Add & select"
                            )}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : !newMediaSearching ? (
              <p className="text-sm text-muted-foreground">
                Enter a search query and click Search.
              </p>
            ) : null}

            <div className="flex justify-start">
              <Button variant="secondary" onClick={() => setStep("review")}>
                Back
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

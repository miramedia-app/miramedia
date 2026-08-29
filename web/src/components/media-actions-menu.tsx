"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, Search, Trash2, Inbox } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { DownloadMediaDialog } from "@/components/download-dialogs/download-media-dialog";
import { SubtitleSearchDialog } from "@/components/subtitle-search-dialog";
import { useUser } from "@/components/providers/user-provider";
import { useFeatures } from "@/components/providers/features-provider";
import apiClient from "@/lib/api/client";
import { getFullyQualifiedMediaName, cn } from "@/lib/utils";
import type { components } from "@/lib/api/api";

type Media = components["schemas"]["PublicMovie"] | components["schemas"]["PublicShow"];

const QUALITY_OPTIONS = [
  { value: "default", label: "Default" },
  { value: "1", label: "4K" },
  { value: "2", label: "1080p" },
  { value: "3", label: "720p" },
  { value: "4", label: "SD" },
] as const;

export function MediaActionsMenu({
  media,
  mediaType,
  before,
  afterSubtitles,
  children,
}: {
  media: Media;
  mediaType: "show" | "movie";
  /** Rendered ahead of Search (first action slot). */
  before?: React.ReactNode;
  afterSubtitles?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user } = useUser();
  const { requests: requestsEnabled, subtitles: subtitlesEnabled } = useFeatures();
  const isSuperuser = !!user?.is_superuser;

  // Search dialog
  const [searchOpen, setSearchOpen] = React.useState(false);

  // Request dialog
  const [requestOpen, setRequestOpen] = React.useState(false);
  const [requesting, setRequesting] = React.useState(false);
  const [requestNote, setRequestNote] = React.useState("");
  const [requestQuality, setRequestQuality] = React.useState("default");

  // Delete dialog
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [deleteFilesOnDisk, setDeleteFilesOnDisk] = React.useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = React.useState("");
  const [deleting, setDeleting] = React.useState(false);

  const deleteConfirmed = deleteConfirmText.toLowerCase() === "delete";

  function resetDeleteState() {
    setDeleteFilesOnDisk(false);
    setDeleteConfirmText("");
  }

  async function handleRequest() {
    setRequesting(true);
    const show = mediaType === "show" ? (media as components["schemas"]["PublicShow"]) : undefined;
    const movie =
      mediaType === "movie" ? (media as components["schemas"]["PublicMovie"]) : undefined;
    const { error } = await apiClient.POST("/api/v1/requests", {
      body: {
        media_type: mediaType,
        title: getFullyQualifiedMediaName(media),
        external_id: String(media.external_id),
        metadata_provider: media.metadata_provider,
        movie_id: movie?.id,
        show_id: show?.id,
        wanted_quality: requestQuality === "default" ? null : Number(requestQuality),
        note: requestNote.trim() || null,
      },
    });
    setRequesting(false);
    if (!error) {
      toast.success(`Request for "${getFullyQualifiedMediaName(media)}" submitted`);
      setRequestOpen(false);
      setRequestNote("");
      setRequestQuality("default");
    } else {
      toast.error("Failed to submit request");
    }
  }

  async function handleDelete() {
    if (!deleteConfirmed) return;
    setDeleting(true);
    try {
      if (mediaType === "show") {
        const { error } = await apiClient.DELETE("/api/v1/shows/{show_id}", {
          params: {
            path: { show_id: media.id! },
            query: { delete_files_on_disk: deleteFilesOnDisk },
          },
        });
        if (error) {
          toast.error("Failed to delete show");
          return;
        }
        toast.success("Show deleted successfully");
        setDeleteOpen(false);
        await queryClient.invalidateQueries({ queryKey: ["shows"] });
        router.push("/dashboard/shows");
      } else {
        const { error } = await apiClient.DELETE("/api/v1/movies/{movie_id}", {
          params: {
            path: { movie_id: media.id! },
            query: { delete_files_on_disk: deleteFilesOnDisk },
          },
        });
        if (error) {
          toast.error("Failed to delete movie");
          return;
        }
        toast.success("Movie deleted successfully");
        setDeleteOpen(false);
        await queryClient.invalidateQueries({ queryKey: ["movies"] });
        router.push("/dashboard/movies");
      }
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {before}
      {isSuperuser && (
        <>
          <Button variant="outline" size="sm" onClick={() => setSearchOpen(true)}>
            <Search className="h-4 w-4" />
            Search
          </Button>
          {searchOpen && (
            <DownloadMediaDialog
              open={searchOpen}
              onOpenChange={setSearchOpen}
              mediaType={mediaType}
              media={media}
            />
          )}

          {subtitlesEnabled &&
            (mediaType === "show" ? (
              <SubtitleSearchDialog
                mode="show"
                showId={media.id ?? ""}
                showName={getFullyQualifiedMediaName(media)}
                hasAllSubtitles={false}
                triggerLabel="Subtitles"
                onUpdate={() => void queryClient.invalidateQueries({ queryKey: ["subtitles"] })}
              />
            ) : (
              <SubtitleSearchDialog
                mode="movie"
                movieId={media.id ?? ""}
                label={getFullyQualifiedMediaName(media)}
                hasSubtitles={false}
                triggerLabel="Subtitles"
                onUpdate={() => void queryClient.invalidateQueries({ queryKey: ["subtitles"] })}
              />
            ))}
        </>
      )}

      {afterSubtitles}

      {isSuperuser && (
        <>
          {children}

          <AlertDialog
            open={deleteOpen}
            onOpenChange={(o) => {
              setDeleteOpen(o);
              if (!o) resetDeleteState();
            }}
          >
            <AlertDialogTrigger
              className={cn(
                buttonVariants({ variant: "destructive", size: "sm" }),
                "border-destructive/30",
              )}
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete — {getFullyQualifiedMediaName(media)}</AlertDialogTitle>
                <AlertDialogDescription>
                  This action cannot be undone. This will permanently delete{" "}
                  <strong>{getFullyQualifiedMediaName(media)}</strong> and all associated data.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <div className="flex flex-col gap-4 py-4">
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="ma-delete-files"
                    checked={deleteFilesOnDisk}
                    onCheckedChange={(v) => setDeleteFilesOnDisk(v === true)}
                  />
                  <Label htmlFor="ma-delete-files" className="text-sm leading-none font-medium">
                    Also delete files on disk
                  </Label>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="ma-delete-confirm" className="text-sm">
                    Type <strong>delete</strong> to confirm
                  </Label>
                  <Input
                    id="ma-delete-confirm"
                    value={deleteConfirmText}
                    onChange={(e) => setDeleteConfirmText(e.target.value)}
                    placeholder="delete"
                    autoComplete="off"
                  />
                </div>
              </div>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <Button
                  variant="destructive"
                  onClick={handleDelete}
                  disabled={!deleteConfirmed || deleting}
                >
                  {deleting && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
                  Delete
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}

      {requestsEnabled && !isSuperuser && (
        <Dialog open={requestOpen} onOpenChange={setRequestOpen}>
          <DialogTrigger render={<Button variant="outline" size="sm" />}>
            <Inbox className="h-4 w-4" />
            Request
          </DialogTrigger>
          <DialogContent className="sm:max-w-[425px]">
            <DialogHeader>
              <DialogTitle>Request {mediaType === "show" ? "Show" : "Movie"}</DialogTitle>
              <DialogDescription>
                Submit a request for <strong>{getFullyQualifiedMediaName(media)}</strong>.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="grid gap-2">
                <Label>Preferred Quality</Label>
                <Select value={requestQuality} onValueChange={setRequestQuality}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {QUALITY_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>Note (optional)</Label>
                <Textarea
                  value={requestNote}
                  onChange={(e) => setRequestNote(e.target.value)}
                  placeholder="Any additional details..."
                />
              </div>
            </div>
            <DialogFooter>
              <Button onClick={handleRequest} disabled={requesting}>
                {requesting ? (
                  <>
                    <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                    Submitting...
                  </>
                ) : (
                  "Submit Request"
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

// silence unused import in some configurations
void cn;

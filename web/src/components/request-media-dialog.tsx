"use client";

import * as React from "react";
import { LoaderCircle } from "lucide-react";
import { toast } from "sonner";
import { Button, type buttonVariants } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import apiClient from "@/lib/api/client";
import type { VariantProps } from "class-variance-authority";

type Variant = NonNullable<VariantProps<typeof buttonVariants>["variant"]>;

export function RequestMediaDialog({
  mediaType,
  title,
  externalId,
  imdbId,
  metadataProvider = "",
  movieId,
  showId,
  seasonNumber,
  variant = "default",
  buttonText = "Request",
  className,
}: {
  mediaType: "movie" | "show";
  title: string;
  externalId: string;
  imdbId?: string;
  metadataProvider?: string;
  movieId?: string;
  showId?: string;
  seasonNumber?: number;
  variant?: Variant;
  buttonText?: string;
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [loading, startLoading] = React.useTransition();
  const [note, setNote] = React.useState("");
  const [wantedQuality, setWantedQuality] = React.useState<string>("default");

  const qualityOptions = [
    { value: "default", label: "Default" },
    { value: "1", label: "4K" },
    { value: "2", label: "1080p" },
    { value: "3", label: "720p" },
    { value: "4", label: "SD" },
  ];

  function submitRequest() {
    startLoading(async () => {
      const { error } = await apiClient.POST("/api/v1/requests", {
        body: {
          media_type: mediaType,
          title,
          external_id: externalId,
          imdb_id: imdbId ?? null,
          metadata_provider: metadataProvider,
          movie_id: movieId,
          show_id: showId,
          season_number: seasonNumber,
          wanted_quality: wantedQuality === "default" ? null : Number(wantedQuality),
          note: note.trim() || null,
        },
      });
      if (!error) {
        toast.success(`Request for "${title}" submitted`);
        setOpen(false);
        setNote("");
        setWantedQuality("default");
      } else {
        toast.error("Failed to submit request");
      }
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant={variant} className={className} />}>
        {buttonText}
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Request {mediaType === "show" ? "Show" : "Movie"}</DialogTitle>
          <DialogDescription>
            Submit a request for <strong>{title}</strong>
            {seasonNumber != null ? ` (Season ${seasonNumber})` : ""}.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label>Preferred Quality</Label>
            <Select value={wantedQuality} onValueChange={setWantedQuality}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {qualityOptions.map((opt) => (
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
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Any additional details..."
            />
          </div>
        </div>
        <DialogFooter>
          <Button onClick={submitRequest} disabled={loading}>
            {loading ? (
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
  );
}

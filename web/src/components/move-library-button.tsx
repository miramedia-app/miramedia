"use client";

import * as React from "react";
import { LoaderCircle, FolderInput } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import apiClient from "@/lib/api/client";
import { useLibraries } from "@/hooks/use-libraries";

export function MoveLibraryButton({
  mediaId,
  mediaType,
  currentLibrary,
}: {
  mediaId: string;
  mediaType: "show" | "movie";
  currentLibrary: string;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [busy, startBusy] = React.useTransition();
  const [target, setTarget] = React.useState("Default");
  const [deleteSource, setDeleteSource] = React.useState(true);

  const librariesQuery = useLibraries(mediaType);
  const libraries = librariesQuery.data ?? [];
  const loadError = librariesQuery.isError ? "Failed to load libraries" : null;

  function move() {
    startBusy(async () => {
      try {
        const { response } =
          mediaType === "show"
            ? await apiClient.POST("/api/v1/shows/{show_id}/move-library", {
                params: {
                  path: { show_id: mediaId },
                  query: { target_library: target, delete_source: deleteSource },
                },
              })
            : await apiClient.POST("/api/v1/movies/{movie_id}/move-library", {
                params: {
                  path: { movie_id: mediaId },
                  query: { target_library: target, delete_source: deleteSource },
                },
              });
        if (!response.ok) {
          const body = await response.text();
          throw new Error(body || response.statusText);
        }
        const data = await response.json();
        if (data.skipped) {
          toast.info(`Skipped: ${data.reason}`);
        } else {
          toast.success(
            `Moved ${data.moved} file(s)` +
              (data.errors?.length ? `, ${data.errors.length} error(s)` : ""),
          );
        }
        setOpen(false);
        await queryClient.invalidateQueries({ queryKey: [mediaType, mediaId] });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "unknown";
        toast.error(`Move failed: ${msg}`);
      }
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) setTarget(currentLibrary || "Default");
      }}
    >
      <DialogTrigger render={<Button variant="outline" />}>
        <FolderInput className="mr-1.5 h-3.5 w-3.5" />
        Move files
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Move {mediaType} files to a different library</DialogTitle>
          <DialogDescription>
            Hardlinks every file to the new library root, then removes the source directory.
            Cross-filesystem moves fall back to a copy.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Target library
            {loadError ? (
              <p className="text-sm text-muted-foreground">{loadError}</p>
            ) : (
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger className="w-[240px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Default">Default</SelectItem>
                  {libraries.map((lib) => (
                    <SelectItem key={lib.name} value={lib.name}>
                      {lib.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={deleteSource}
              onChange={(e) => setDeleteSource(e.target.checked)}
            />
            Delete source directory after successful move
          </label>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={move} disabled={busy || !!loadError || target === currentLibrary}>
            {busy && <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Move
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

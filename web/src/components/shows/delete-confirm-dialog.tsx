"use client";

import * as React from "react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import type { DeleteTarget } from "@/lib/show-detail";

export interface DeleteConfirmDialogProps {
  target: DeleteTarget | null;
  confirmText: string;
  onConfirmTextChange: (value: string) => void;
  confirmed: boolean;
  deleting: boolean;
  blockSource: boolean;
  onBlockSourceChange: (checked: boolean) => void;
  selectedFilesCount: number;
  selectedTorrentsCount: number;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * "Type delete to confirm" modal for every destructive show-detail action.
 * Copy is keyed off the discriminated `DeleteTarget` type.
 */
export function DeleteConfirmDialog({
  target,
  confirmText,
  onConfirmTextChange,
  confirmed,
  deleting,
  blockSource,
  onBlockSourceChange,
  selectedFilesCount,
  selectedTorrentsCount,
  onClose,
  onConfirm,
}: DeleteConfirmDialogProps) {
  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {target?.type === "season" && "Delete season files?"}
            {target?.type === "episode" && "Delete episode files?"}
            {target?.type === "subtitle" && "Delete subtitle file?"}
            {target?.type === "torrent" && "Delete torrent?"}
            {target?.type === "bulk-files" && (
              <>
                Delete {selectedFilesCount} selected file{selectedFilesCount !== 1 ? "s" : ""}?
              </>
            )}
            {target?.type === "bulk-torrents" && (
              <>
                Delete {selectedTorrentsCount} selected torrent
                {selectedTorrentsCount !== 1 ? "s" : ""}?
              </>
            )}
            {target?.type === "file" && "Delete file?"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {target?.type === "season" &&
              "This will delete all files for this season from disk and mark all episodes as skipped. This cannot be undone."}
            {target?.type === "episode" &&
              "This will delete all files for this episode from disk and mark it as skipped. This cannot be undone."}
            {target?.type === "torrent" &&
              "This will delete the torrent and its downloaded files. This cannot be undone."}
            {target?.type === "bulk-files" &&
              "This will permanently delete the selected files from disk. This cannot be undone."}
            {target?.type === "bulk-torrents" &&
              "This will delete the selected torrents and their downloaded files. This cannot be undone."}
            {(target?.type === "file" || target?.type === "subtitle") &&
              "This will permanently delete the file from disk. This cannot be undone."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-2 py-2">
          {target?.type === "file" && target.sourceInfoHash && (
            <div className="flex items-center gap-2">
              <Checkbox
                id="block-source-episode"
                checked={blockSource}
                onCheckedChange={(checked) => onBlockSourceChange(checked === true)}
              />
              <Label htmlFor="block-source-episode">Add source torrent to deny list</Label>
            </div>
          )}
          <Label htmlFor="delete-confirm">
            Type <strong>delete</strong> to confirm
          </Label>
          <Input
            id="delete-confirm"
            value={confirmText}
            onChange={(e) => onConfirmTextChange(e.target.value)}
            placeholder="delete"
            autoComplete="off"
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>Cancel</AlertDialogCancel>
          <Button variant="destructive" disabled={!confirmed || deleting} onClick={onConfirm}>
            {deleting ? "Deleting…" : "Delete"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

"use client";

import * as React from "react";
import { Plus, X } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatusPill } from "@/components/ui/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Site } from "@/lib/indexers";

export interface ManageUrlsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  urlSite: Site | null;
  newUrl: string;
  setNewUrl: (value: string) => void;
  loading: boolean;
  onSwitchActive: (url: string) => void;
  onAddUrl: () => void;
  onRemoveUrl: (url: string) => void;
}

/** Dialog for selecting the active URL and managing mirror URLs of a site. */
export function ManageUrlsDialog({
  open,
  onOpenChange,
  urlSite,
  newUrl,
  setNewUrl,
  loading,
  onSwitchActive,
  onAddUrl,
  onRemoveUrl,
}: ManageUrlsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Manage URLs — {urlSite?.name}</DialogTitle>
          <DialogDescription>
            Select the active URL or add custom mirrors. The active URL is used for searches.
          </DialogDescription>
        </DialogHeader>
        {urlSite && (
          <div className="grid gap-3 py-4">
            {(urlSite.available_urls ?? []).map((url) => (
              <div key={url} className="flex items-center gap-2 rounded-md border p-2">
                <button
                  type="button"
                  className={`flex-1 truncate text-left text-sm ${
                    url === urlSite.url ? "font-semibold text-foreground" : "text-muted-foreground"
                  }`}
                  onClick={() => void onSwitchActive(url)}
                  disabled={loading}
                >
                  {url}
                </button>
                {url === urlSite.url ? (
                  <StatusPill status="active" label="Active" className="shrink-0" />
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 shrink-0 p-0"
                    onClick={() => void onRemoveUrl(url)}
                    disabled={loading}
                  >
                    <X className="h-3 w-3" />
                  </Button>
                )}
              </div>
            ))}
            <div className="flex gap-2">
              <Input
                value={newUrl}
                onChange={(e) => setNewUrl(e.target.value)}
                placeholder="https://mirror.example.com"
                className="flex-1"
                onKeyDown={(e) => {
                  if (e.key === "Enter") void onAddUrl();
                }}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={() => void onAddUrl()}
                disabled={loading || !newUrl.trim()}
              >
                <Plus className="mr-1 h-3 w-3" />
                Add
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

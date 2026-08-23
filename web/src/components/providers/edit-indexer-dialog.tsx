"use client";

import * as React from "react";
import { LoaderCircle } from "lucide-react";

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
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import type { Site } from "@/lib/indexers";

export interface EditIndexerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editSite: Site | null;
  setEditSite: React.Dispatch<React.SetStateAction<Site | null>>;
  loading: boolean;
  onSave: () => void;
  cfEnabled: boolean;
}

/** Dialog for editing an existing indexer's settings. */
export function EditIndexerDialog({
  open,
  onOpenChange,
  editSite,
  setEditSite,
  loading,
  onSave,
  cfEnabled,
}: EditIndexerDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Edit Indexer</DialogTitle>
          <DialogDescription>Modify settings for this indexer.</DialogDescription>
        </DialogHeader>
        {editSite && (
          <div className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="edit-indexer-name">Name</Label>
              <Input
                id="edit-indexer-name"
                value={editSite.name}
                onChange={(e) => setEditSite((s) => (s ? { ...s, name: e.target.value } : s))}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-indexer-url">Active URL</Label>
              {(editSite.available_urls ?? []).length > 1 ? (
                <Select
                  value={editSite.url}
                  onValueChange={(v) => setEditSite((s) => (s ? { ...s, url: v } : s))}
                >
                  <SelectTrigger id="edit-indexer-url" className="w-full">
                    <span className="truncate">{editSite.url}</span>
                  </SelectTrigger>
                  <SelectContent>
                    {(editSite.available_urls ?? []).map((url) => (
                      <SelectItem key={url} value={url}>
                        {url}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  id="edit-indexer-url"
                  value={editSite.url}
                  onChange={(e) => setEditSite((s) => (s ? { ...s, url: e.target.value } : s))}
                />
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-indexer-priority">Priority</Label>
              <Input
                id="edit-indexer-priority"
                type="number"
                min={0}
                value={editSite.priority ?? 100}
                onChange={(e) =>
                  setEditSite((s) => (s ? { ...s, priority: parseInt(e.target.value, 10) } : s))
                }
              />
              <span className="text-xs text-muted-foreground">Lower = searched first.</span>
            </div>
            {editSite.site_type === "torznab" && (
              <div className="space-y-2">
                <Label htmlFor="edit-indexer-key">API Key</Label>
                <Input
                  id="edit-indexer-key"
                  value={editSite.api_key ?? ""}
                  onChange={(e) => setEditSite((s) => (s ? { ...s, api_key: e.target.value } : s))}
                  type="password"
                />
              </div>
            )}
            <div className="rounded-lg border bg-muted/30 p-4">
              <div className="flex flex-col gap-4">
                <div className="flex items-center justify-between gap-4">
                  <div className="space-y-0.5">
                    <Label htmlFor="edit-indexer-enabled">Enabled</Label>
                    <p className="text-xs text-muted-foreground">
                      Include this indexer in searches.
                    </p>
                  </div>
                  <Switch
                    id="edit-indexer-enabled"
                    checked={editSite.enabled}
                    onCheckedChange={(v) => setEditSite((s) => (s ? { ...s, enabled: v } : s))}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between gap-4">
                  <div className="space-y-0.5">
                    <Label htmlFor="edit-indexer-tv">Shows</Label>
                    <p className="text-xs text-muted-foreground">
                      Search this indexer for TV shows.
                    </p>
                  </div>
                  <Switch
                    id="edit-indexer-tv"
                    checked={editSite.supports_tv}
                    onCheckedChange={(v) => setEditSite((s) => (s ? { ...s, supports_tv: v } : s))}
                  />
                </div>
                <Separator />
                <div className="flex items-center justify-between gap-4">
                  <div className="space-y-0.5">
                    <Label htmlFor="edit-indexer-movies">Movies</Label>
                    <p className="text-xs text-muted-foreground">Search this indexer for movies.</p>
                  </div>
                  <Switch
                    id="edit-indexer-movies"
                    checked={editSite.supports_movies}
                    onCheckedChange={(v) =>
                      setEditSite((s) => (s ? { ...s, supports_movies: v } : s))
                    }
                  />
                </div>
                {cfEnabled && (
                  <>
                    <Separator />
                    <div className="flex items-center justify-between gap-4">
                      <div className="space-y-0.5">
                        <Label htmlFor="edit-indexer-cf">Cloudflare</Label>
                        <p className="text-xs text-muted-foreground">
                          Bypass Cloudflare when searching.
                        </p>
                      </div>
                      <Switch
                        id="edit-indexer-cf"
                        checked={editSite.cloudflare_protected}
                        onCheckedChange={(v) =>
                          setEditSite((s) => (s ? { ...s, cloudflare_protected: v } : s))
                        }
                      />
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={() => void onSave()}
            disabled={loading || !editSite?.name || !editSite?.url}
            className="border border-white bg-white text-black hover:bg-white/90"
          >
            {loading ? (
              <>
                <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

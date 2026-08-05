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
}

/** Dialog for editing an existing indexer site's settings. */
export function EditIndexerDialog({
  open,
  onOpenChange,
  editSite,
  setEditSite,
  loading,
  onSave,
}: EditIndexerDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Edit Indexer Site</DialogTitle>
          <DialogDescription>Modify settings for this indexer site.</DialogDescription>
        </DialogHeader>
        {editSite && (
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label>Name</Label>
              <Input
                value={editSite.name}
                onChange={(e) => setEditSite((s) => (s ? { ...s, name: e.target.value } : s))}
              />
            </div>
            <div className="grid gap-2">
              <Label>Active URL</Label>
              {(editSite.available_urls ?? []).length > 1 ? (
                <Select
                  value={editSite.url}
                  onValueChange={(v) => setEditSite((s) => (s ? { ...s, url: v } : s))}
                >
                  <SelectTrigger>
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
                  value={editSite.url}
                  onChange={(e) => setEditSite((s) => (s ? { ...s, url: e.target.value } : s))}
                />
              )}
            </div>
            <div className="grid gap-2">
              <Label>Priority</Label>
              <Input
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
              <div className="grid gap-2">
                <Label>API Key</Label>
                <Input
                  value={editSite.api_key ?? ""}
                  onChange={(e) => setEditSite((s) => (s ? { ...s, api_key: e.target.value } : s))}
                  type="password"
                />
              </div>
            )}
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Switch
                  checked={editSite.supports_tv}
                  onCheckedChange={(v) => setEditSite((s) => (s ? { ...s, supports_tv: v } : s))}
                />
                <Label>Shows</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={editSite.supports_movies}
                  onCheckedChange={(v) =>
                    setEditSite((s) => (s ? { ...s, supports_movies: v } : s))
                  }
                />
                <Label>Movies</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={editSite.cloudflare_protected}
                  onCheckedChange={(v) =>
                    setEditSite((s) => (s ? { ...s, cloudflare_protected: v } : s))
                  }
                />
                <Label>CF Protected</Label>
              </div>
            </div>
          </div>
        )}
        <DialogFooter>
          <Button
            onClick={() => void onSave()}
            disabled={loading || !editSite?.name || !editSite?.url}
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

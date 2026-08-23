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
import type { NewSiteForm } from "@/hooks/use-indexer-sites";

export interface AddIndexerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  newSite: NewSiteForm;
  setNewSite: React.Dispatch<React.SetStateAction<NewSiteForm>>;
  loading: boolean;
  onAdd: () => void;
  cfEnabled: boolean;
}

/** Dialog for adding a custom Torznab indexer. */
export function AddIndexerDialog({
  open,
  onOpenChange,
  newSite,
  setNewSite,
  loading,
  onAdd,
  cfEnabled,
}: AddIndexerDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Add Indexer</DialogTitle>
          <DialogDescription>Add a custom Torznab-compatible indexer.</DialogDescription>
        </DialogHeader>
        <div className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="add-indexer-name">Name</Label>
            <Input
              id="add-indexer-name"
              value={newSite.name}
              onChange={(e) => setNewSite((s) => ({ ...s, name: e.target.value }))}
              placeholder="My Private Tracker"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="add-indexer-url">Torznab URL</Label>
            <Input
              id="add-indexer-url"
              value={newSite.url}
              onChange={(e) => setNewSite((s) => ({ ...s, url: e.target.value }))}
              placeholder="https://tracker.example.com/torznab"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="add-indexer-key">API Key</Label>
            <Input
              id="add-indexer-key"
              value={newSite.api_key}
              onChange={(e) => setNewSite((s) => ({ ...s, api_key: e.target.value }))}
              placeholder="Optional"
              type="password"
            />
          </div>
          <div className="rounded-lg border bg-muted/30 p-4">
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between gap-4">
                <div className="space-y-0.5">
                  <Label htmlFor="add-indexer-enabled">Enabled</Label>
                  <p className="text-xs text-muted-foreground">Include this indexer in searches.</p>
                </div>
                <Switch
                  id="add-indexer-enabled"
                  checked={newSite.enabled}
                  onCheckedChange={(v) => setNewSite((s) => ({ ...s, enabled: v }))}
                />
              </div>
              <Separator />
              <div className="flex items-center justify-between gap-4">
                <div className="space-y-0.5">
                  <Label htmlFor="add-indexer-tv">Shows</Label>
                  <p className="text-xs text-muted-foreground">Search this indexer for TV shows.</p>
                </div>
                <Switch
                  id="add-indexer-tv"
                  checked={newSite.supports_tv}
                  onCheckedChange={(v) => setNewSite((s) => ({ ...s, supports_tv: v }))}
                />
              </div>
              <Separator />
              <div className="flex items-center justify-between gap-4">
                <div className="space-y-0.5">
                  <Label htmlFor="add-indexer-movies">Movies</Label>
                  <p className="text-xs text-muted-foreground">Search this indexer for movies.</p>
                </div>
                <Switch
                  id="add-indexer-movies"
                  checked={newSite.supports_movies}
                  onCheckedChange={(v) => setNewSite((s) => ({ ...s, supports_movies: v }))}
                />
              </div>
              {cfEnabled && (
                <>
                  <Separator />
                  <div className="flex items-center justify-between gap-4">
                    <div className="space-y-0.5">
                      <Label htmlFor="add-indexer-cf">Cloudflare</Label>
                      <p className="text-xs text-muted-foreground">
                        Bypass Cloudflare when searching.
                      </p>
                    </div>
                    <Switch
                      id="add-indexer-cf"
                      checked={newSite.cloudflare_protected}
                      onCheckedChange={(v) =>
                        setNewSite((s) => ({ ...s, cloudflare_protected: v }))
                      }
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={() => void onAdd()}
            disabled={loading || !newSite.name || !newSite.url}
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

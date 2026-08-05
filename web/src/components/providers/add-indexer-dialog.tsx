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
import type { NewSiteForm } from "@/hooks/use-indexer-sites";

export interface AddIndexerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  newSite: NewSiteForm;
  setNewSite: React.Dispatch<React.SetStateAction<NewSiteForm>>;
  loading: boolean;
  onAdd: () => void;
}

/** Dialog for adding a custom Torznab indexer site. */
export function AddIndexerDialog({
  open,
  onOpenChange,
  newSite,
  setNewSite,
  loading,
  onAdd,
}: AddIndexerDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Add Indexer Site</DialogTitle>
          <DialogDescription>Add a custom Torznab-compatible indexer site.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label>Name</Label>
            <Input
              value={newSite.name}
              onChange={(e) => setNewSite((s) => ({ ...s, name: e.target.value }))}
              placeholder="My Private Tracker"
            />
          </div>
          <div className="grid gap-2">
            <Label>Torznab URL</Label>
            <Input
              value={newSite.url}
              onChange={(e) => setNewSite((s) => ({ ...s, url: e.target.value }))}
              placeholder="https://tracker.example.com/torznab"
            />
          </div>
          <div className="grid gap-2">
            <Label>API Key</Label>
            <Input
              value={newSite.api_key}
              onChange={(e) => setNewSite((s) => ({ ...s, api_key: e.target.value }))}
              placeholder="Optional"
              type="password"
            />
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Switch
                checked={newSite.supports_tv}
                onCheckedChange={(v) => setNewSite((s) => ({ ...s, supports_tv: v }))}
              />
              <Label>Shows</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={newSite.supports_movies}
                onCheckedChange={(v) => setNewSite((s) => ({ ...s, supports_movies: v }))}
              />
              <Label>Movies</Label>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={newSite.cloudflare_protected}
                onCheckedChange={(v) => setNewSite((s) => ({ ...s, cloudflare_protected: v }))}
              />
              <Label>CF Protected</Label>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button onClick={() => void onAdd()} disabled={loading || !newSite.name || !newSite.url}>
            {loading ? (
              <>
                <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                Adding...
              </>
            ) : (
              "Add Site"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

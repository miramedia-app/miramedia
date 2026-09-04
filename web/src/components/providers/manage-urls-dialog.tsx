"use client";

import * as React from "react";
import { ChevronDown, ChevronUp, Plus, X } from "lucide-react";

import {
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogDescription,
  ResponsiveDialogFooter,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
} from "@/components/ui/responsive-dialog";
import { StatusPill } from "@/components/ui/status-pill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { MirrorEntry, Site } from "@/lib/indexers";

export interface ManageUrlsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  urlSite: Site | null;
  mirrors: MirrorEntry[];
  activeUrl: string;
  newUrl: string;
  setNewUrl: (value: string) => void;
  loading: boolean;
  onSetActive: (url: string) => void;
  onToggle: (url: string) => void;
  onMove: (index: number, dir: -1 | 1) => void;
  onAddUrl: () => void;
  onRemoveUrl: (url: string) => void;
  onSave: () => void;
}

/**
 * Manage a native indexer's failover mirrors: reorder, enable/disable, choose
 * the active one, add custom mirrors, and delete user-added ones. Built-in
 * ("seeded") mirrors can be reordered and disabled but not deleted. Edits are
 * staged locally and committed with Save.
 */
export function ManageUrlsDialog({
  open,
  onOpenChange,
  urlSite,
  mirrors,
  activeUrl,
  newUrl,
  setNewUrl,
  loading,
  onSetActive,
  onToggle,
  onMove,
  onAddUrl,
  onRemoveUrl,
  onSave,
}: ManageUrlsDialogProps) {
  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange}>
      <ResponsiveDialogContent className="sm:max-w-[560px]">
        <ResponsiveDialogHeader>
          <ResponsiveDialogTitle>Manage Mirrors — {urlSite?.name}</ResponsiveDialogTitle>
          <ResponsiveDialogDescription>
            Reorder, enable, or disable mirrors. Only enabled mirrors are searched, in this order.
            The active mirror is tried first. Built-in mirrors can be disabled but not deleted.
          </ResponsiveDialogDescription>
        </ResponsiveDialogHeader>
        {urlSite && (
          <div className="grid gap-2 py-2">
            {mirrors.map((mirror, index) => {
              const isActive = mirror.url === activeUrl;
              return (
                <div key={mirror.url} className="flex items-center gap-2 rounded-md border p-2">
                  <div className="flex flex-col">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-4 w-6 p-0"
                      onClick={() => onMove(index, -1)}
                      disabled={loading || index === 0}
                      aria-label="Move up"
                    >
                      <ChevronUp className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-4 w-6 p-0"
                      onClick={() => onMove(index, 1)}
                      disabled={loading || index === mirrors.length - 1}
                      aria-label="Move down"
                    >
                      <ChevronDown className="h-3 w-3" />
                    </Button>
                  </div>
                  <button
                    type="button"
                    className={`flex-1 truncate text-left text-sm ${
                      isActive ? "font-semibold text-foreground" : "text-muted-foreground"
                    } ${mirror.enabled ? "" : "line-through opacity-60"}`}
                    onClick={() => onSetActive(mirror.url)}
                    disabled={loading || isActive}
                    title={isActive ? mirror.url : `Set active: ${mirror.url}`}
                  >
                    {mirror.url}
                  </button>
                  {mirror.source === "seeded" && (
                    <StatusPill status="idle" label="Built-in" className="shrink-0" />
                  )}
                  {isActive ? (
                    <StatusPill status="active" label="Active" className="shrink-0" />
                  ) : (
                    <Switch
                      checked={mirror.enabled}
                      onCheckedChange={() => onToggle(mirror.url)}
                      disabled={loading}
                      aria-label={mirror.enabled ? "Disable mirror" : "Enable mirror"}
                    />
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 shrink-0 p-0"
                    onClick={() => onRemoveUrl(mirror.url)}
                    disabled={loading || isActive || mirror.source === "seeded"}
                    aria-label="Delete mirror"
                    title={
                      mirror.source === "seeded"
                        ? "Built-in mirrors can't be deleted — disable instead"
                        : "Delete mirror"
                    }
                  >
                    <X className="h-3 w-3" />
                  </Button>
                </div>
              );
            })}
            <div className="flex gap-2">
              <Input
                value={newUrl}
                onChange={(e) => setNewUrl(e.target.value)}
                placeholder="https://mirror.example.com"
                className="flex-1"
                onKeyDown={(e) => {
                  if (e.key === "Enter") onAddUrl();
                }}
              />
              <Button
                variant="outline"
                className="gap-1"
                onClick={() => onAddUrl()}
                disabled={loading || !newUrl.trim()}
              >
                <Plus className="h-3 w-3" />
                Add
              </Button>
            </div>
          </div>
        )}
        <ResponsiveDialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Button onClick={() => onSave()} disabled={loading}>
            Save
          </Button>
        </ResponsiveDialogFooter>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}

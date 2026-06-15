"use client";

import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import apiClient from "@/lib/api/client";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

export function ImportsTab({
  imports,
  setImportsPath,
  misc,
  setMiscPath,
}: {
  imports: AnyObj;
  setImportsPath: SetPath;
  misc: AnyObj;
  setMiscPath: SetPath;
}) {
  const imp = imports;
  const m = misc;
  const qc = useQueryClient();
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Import Settings</CardTitle>
          <CardDescription>
            How the library scanner matches and imports directories found in your library roots.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5 pr-4">
                <Label className="text-sm font-medium">Automatic Library Scan</Label>
                <p className="text-xs text-muted-foreground">
                  Periodically scan your library roots in the background and feed new directories
                  into the Imports page. This does not change the Movies or Shows pages — detected
                  items only appear under Imports.
                </p>
              </div>
              <Switch
                checked={Boolean(imp.auto_scan_enabled)}
                onCheckedChange={(v) => setImportsPath(["auto_scan_enabled"], v)}
              />
            </div>
            <div className="space-y-2">
              <Label>
                Scan interval (hours)
                <OverrideMarker path={["imports", "auto_scan_interval_hours"]} />
              </Label>
              <Input
                type="number"
                min={1}
                value={Number(imp.auto_scan_interval_hours ?? "") || ""}
                onChange={(e) =>
                  setImportsPath(["auto_scan_interval_hours"], Number(e.target.value) || 0)
                }
              />
              <p className="text-xs text-muted-foreground">
                Changing this reschedules the background scan without a restart.
              </p>
            </div>
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5 pr-4">
                <Label className="text-sm font-medium">Automatic Import on Scan</Label>
                <p className="text-xs text-muted-foreground">
                  When enabled, the library scan creates (if needed) and imports the single
                  highest-confidence match — existing library item or provider hit — without human
                  review, as long as it clears the confidence threshold below.
                </p>
              </div>
              <Switch
                checked={Boolean(imp.auto_import_on_scan)}
                onCheckedChange={(v) => setImportsPath(["auto_import_on_scan"], v)}
              />
            </div>
            <div className="space-y-2">
              <Label>
                Minimum confidence to auto-import (0–1)
                <OverrideMarker path={["imports", "auto_import_min_confidence"]} />
              </Label>
              <Input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={
                  imp.auto_import_min_confidence === undefined
                    ? ""
                    : Number(imp.auto_import_min_confidence)
                }
                onChange={(e) =>
                  setImportsPath(["auto_import_min_confidence"], Number(e.target.value) || 0)
                }
              />
              <p className="text-xs text-muted-foreground">
                Lower values import more aggressively but risk wrong matches. 0.9 is conservative;
                1.0 requires an exact title + year match.
              </p>
            </div>
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5 pr-4">
                <Label className="text-sm font-medium">Provider Matching</Label>
                <p className="text-xs text-muted-foreground">
                  When a scanned directory has no strong match against a tracked show/movie, query
                  the metadata provider by the detected name and year and surface the hits as
                  pickable candidates.
                </p>
              </div>
              <Switch
                checked={Boolean(imp.provider_search_on_scan ?? true)}
                onCheckedChange={(v) => setImportsPath(["provider_search_on_scan"], v)}
              />
            </div>
            <div className="space-y-2">
              <Label>
                Max provider results per directory
                <OverrideMarker path={["imports", "provider_search_max_results"]} />
              </Label>
              <Input
                type="number"
                min={1}
                value={Number(imp.provider_search_max_results ?? "") || ""}
                onChange={(e) =>
                  setImportsPath(["provider_search_max_results"], Number(e.target.value) || 0)
                }
              />
            </div>
            <div className="space-y-2">
              <Label>
                Auto-pick threshold (0–1)
                <OverrideMarker path={["misc", "auto_pick_confidence_threshold"]} />
              </Label>
              <Input
                type="number"
                step="0.05"
                min={0}
                max={1}
                value={Number(m.auto_pick_confidence_threshold ?? "") || ""}
                onChange={(e) =>
                  setMiscPath(["auto_pick_confidence_threshold"], Number(e.target.value) || 0)
                }
                placeholder="0.8"
                className="max-w-[120px]"
              />
              <p className="text-xs text-muted-foreground">
                When a scanned directory&apos;s top existing-library match meets this confidence,
                provider search is skipped. Default 0.8.
              </p>
            </div>
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">Scan Cache</Label>
              <p className="text-xs text-muted-foreground">
                Persisted output of the most recent library scans. Clear it to drop stale
                <span className="font-mono"> imported </span>/
                <span className="font-mono"> failed </span>
                rows left behind by prior runs and force the next scan to re-evaluate every
                directory from scratch.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={async () => {
                if (
                  !confirm(
                    "Clear the library scan cache? This drops all cached scan results; the next Scan now will rebuild the list.",
                  )
                )
                  return;
                try {
                  const { error } = await apiClient.DELETE("/api/v1/imports/scan/cache");
                  if (error) {
                    toast.error("Failed to clear scan cache");
                    return;
                  }
                  toast.success("Scan cache cleared");
                  await qc.invalidateQueries({ queryKey: ["imports"] });
                } catch {
                  toast.error("Failed to clear scan cache");
                }
              }}
            >
              Clear scan cache
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

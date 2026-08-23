"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { AnyObj, SetPath } from "../_shared";

export function PlaybackTab({
  streams,
  setStreamsPath,
  playback,
  setPlaybackPath,
}: {
  streams: AnyObj;
  setStreamsPath: SetPath;
  playback: AnyObj;
  setPlaybackPath: SetPath;
}) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Playback Settings</CardTitle>
          <CardDescription>
            In-browser playback, the HLS transcode cache, and resume rows.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between gap-4">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">Streaming</Label>
                <p className="text-xs text-muted-foreground">
                  Allow playing media in the browser. When off, play buttons are hidden and stream
                  endpoints are disabled.
                </p>
              </div>
              <Switch
                checked={Boolean(streams.enabled ?? true)}
                onCheckedChange={(v) => setStreamsPath(["enabled"], v)}
              />
            </div>
            <div
              className={`flex items-center justify-between gap-4 border-t pt-3 ${
                (streams.enabled ?? true) ? "" : "opacity-50"
              }`}
            >
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">Continue Watching</Label>
                <p className="text-xs text-muted-foreground">
                  In-progress movies and episodes on the dashboard. Disabling hides the row.
                </p>
              </div>
              <Switch
                checked={Boolean(playback.continue_watching ?? true)}
                onCheckedChange={(v) => setPlaybackPath(["continue_watching"], v)}
                disabled={!(streams.enabled ?? true)}
              />
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>HLS cache max size (GB)</Label>
              <Input
                type="number"
                min={0}
                step="any"
                value={Number(streams.hls_cache_max_gb ?? 0)}
                onChange={(e) => setStreamsPath(["hls_cache_max_gb"], Number(e.target.value) || 0)}
                placeholder="0"
              />
            </div>
            <div className="space-y-2">
              <Label>HLS cache max age (days)</Label>
              <Input
                type="number"
                min={0}
                value={Number(streams.hls_cache_max_age_days ?? 0)}
                onChange={(e) =>
                  setStreamsPath(["hls_cache_max_age_days"], Number(e.target.value) || 0)
                }
                placeholder="0"
              />
            </div>
          </div>
          <div className="flex items-center justify-between gap-4 rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">Downloads</Label>
              <p className="text-xs text-muted-foreground">
                Allow downloading media files from the player. Disabling hides the Download buttons
                and blocks download requests. Independent of Streaming.
              </p>
            </div>
            <Switch
              checked={Boolean(streams.downloads ?? true)}
              onCheckedChange={(v) => setStreamsPath(["downloads"], v)}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

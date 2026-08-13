"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { AnyObj, SetPath } from "../_shared";

export function WatchlistsTab({
  watchlists,
  setWatchlistsPath,
}: {
  watchlists: AnyObj;
  setWatchlistsPath: SetPath;
}) {
  const wl = watchlists;
  const native = (wl.native as AnyObj | undefined) ?? {};

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Watchlist Settings</CardTitle>
          <CardDescription>
            Shared options for custom lists, Watch Next, Upcoming, and Continue Watching. Watchlists
            are active whenever Native Watchlists below is enabled.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">Continue Watching</Label>
              <p className="text-xs text-muted-foreground">
                In-progress movies and episodes on the dashboard. Disabling hides the row.
              </p>
            </div>
            <Switch
              checked={Boolean(wl.continue_watching ?? true)}
              onCheckedChange={(v) => setWatchlistsPath(["continue_watching"], v)}
            />
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">Auto-remove watched items</Label>
              <p className="text-xs text-muted-foreground">
                When a movie or episode is marked watched (or finishes playback), remove matching
                items from custom lists. Marking a whole show watched also removes show entries.
              </p>
            </div>
            <Switch
              checked={Boolean(wl.auto_remove_watched)}
              onCheckedChange={(v) => setWatchlistsPath(["auto_remove_watched"], v)}
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>Max lists per user</Label>
              <Input
                type="number"
                min={0}
                value={Number(wl.max_lists_per_user ?? 0)}
                onChange={(e) =>
                  setWatchlistsPath(["max_lists_per_user"], Number(e.target.value) || 0)
                }
                placeholder="0"
              />
              <p className="text-xs text-muted-foreground">0 = unlimited.</p>
            </div>
            <div className="space-y-2">
              <Label>Max items per list</Label>
              <Input
                type="number"
                min={0}
                value={Number(wl.max_items_per_list ?? 0)}
                onChange={(e) =>
                  setWatchlistsPath(["max_items_per_list"], Number(e.target.value) || 0)
                }
                placeholder="0"
              />
              <p className="text-xs text-muted-foreground">0 = unlimited.</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-4">
            <div>
              <CardTitle>Native Watchlists</CardTitle>
              <CardDescription>
                Built-in custom lists, Watch Next, and Upcoming. No external service required.
                Disabling hides the Watchlists sidebar link.
              </CardDescription>
            </div>
            <Switch
              checked={Boolean(native.enabled ?? true)}
              onCheckedChange={(v) => setWatchlistsPath(["native", "enabled"], v)}
            />
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center justify-between gap-4 rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">Custom lists</Label>
              <p className="text-xs text-muted-foreground">
                User-created private lists on the Watchlists hub. Disabling hides Create list and
                Add to Watchlist.
              </p>
            </div>
            <Switch
              checked={Boolean(native.custom_lists ?? true)}
              onCheckedChange={(v) => setWatchlistsPath(["native", "custom_lists"], v)}
            />
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between gap-4">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">Watch Next</Label>
                <p className="text-xs text-muted-foreground">
                  Next downloaded episode for each started show. Shown as a pinned hub card.
                </p>
              </div>
              <Switch
                checked={Boolean(native.watch_next ?? true)}
                onCheckedChange={(v) => setWatchlistsPath(["native", "watch_next"], v)}
              />
            </div>
            <div className="flex items-center justify-between gap-4 border-t pt-3">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">Include specials by default</Label>
                <p className="text-xs text-muted-foreground">
                  When on, Watch Next includes season 0 / specials unless a request overrides it.
                </p>
              </div>
              <Switch
                checked={Boolean(native.watch_next_include_specials)}
                onCheckedChange={(v) =>
                  setWatchlistsPath(["native", "watch_next_include_specials"], v)
                }
              />
            </div>
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between gap-4">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">Upcoming</Label>
                <p className="text-xs text-muted-foreground">
                  Release schedule for tracked library items. Shown as a pinned hub card.
                </p>
              </div>
              <Switch
                checked={Boolean(native.upcoming ?? true)}
                onCheckedChange={(v) => setWatchlistsPath(["native", "upcoming"], v)}
              />
            </div>
            <div className="grid gap-4 border-t pt-3 md:grid-cols-2">
              <div className="space-y-2">
                <Label>Default past days</Label>
                <Input
                  type="number"
                  min={0}
                  value={Number(native.upcoming_default_past_days ?? 0)}
                  onChange={(e) =>
                    setWatchlistsPath(
                      ["native", "upcoming_default_past_days"],
                      Number(e.target.value) || 0,
                    )
                  }
                  placeholder="0"
                />
              </div>
              <div className="space-y-2">
                <Label>Default future days</Label>
                <Input
                  type="number"
                  min={0}
                  value={Number(native.upcoming_default_future_days ?? 30)}
                  onChange={(e) =>
                    setWatchlistsPath(
                      ["native", "upcoming_default_future_days"],
                      Number(e.target.value) || 0,
                    )
                  }
                  placeholder="30"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Initial Upcoming date window when no range is chosen (e.g. today → +30).
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

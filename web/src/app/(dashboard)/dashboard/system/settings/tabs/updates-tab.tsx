"use client";

import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";
import apiClient from "@/lib/api/client";

export function UpdatesTab({
  updates,
  setUpdatesPath,
}: {
  updates: AnyObj;
  setUpdatesPath: SetPath;
}) {
  const qc = useQueryClient();
  const upd = updates;

  const updateInfoQuery = useQuery({
    queryKey: ["system", "updates"],
    queryFn: async ({ signal }) => {
      const { data } = await apiClient.GET("/api/v1/system/updates", { signal });
      return data ?? null;
    },
    retry: false,
  });
  const applyStatusQuery = useQuery({
    queryKey: ["system", "updates", "status"],
    queryFn: async ({ signal }) => {
      const { data } = await apiClient.GET("/api/v1/system/updates/status", { signal });
      return data ?? null;
    },
    retry: false,
    refetchInterval: (q) => {
      const s = (q.state.data as AnyObj | null | undefined)?.state;
      return s === "pulling" || s === "checking" || s === "restarting" ? 2000 : false;
    },
    refetchIntervalInBackground: false,
  });

  const info = updateInfoQuery.data as AnyObj | null | undefined;
  const status = applyStatusQuery.data as AnyObj | null | undefined;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Update Status</CardTitle>
          <CardDescription>Current build, latest release, and apply controls.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {updateInfoQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : !info ? (
            <p className="text-sm text-muted-foreground">Could not fetch update info.</p>
          ) : (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-0.5">
                  <p className="text-xs text-muted-foreground">Current version</p>
                  <p className="font-mono text-sm">
                    {(info.current_version as string) ?? "unknown"}
                  </p>
                </div>
                <div className="space-y-0.5">
                  <p className="text-xs text-muted-foreground">Latest version</p>
                  <p className="font-mono text-sm">
                    {(info.latest_version as string) ?? "unknown"}
                  </p>
                </div>
                <div className="space-y-0.5">
                  <p className="text-xs text-muted-foreground">Last checked</p>
                  <p className="text-sm">
                    {(() => {
                      const t = info.last_checked_at as string | null;
                      return t ? new Date(t).toLocaleString() : "never";
                    })()}
                  </p>
                </div>
                <div className="space-y-0.5">
                  <p className="text-xs text-muted-foreground">Repo</p>
                  <p className="font-mono text-sm">{info.repo as string}</p>
                </div>
              </div>
              {info.update_available ? (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
                  Update available.{" "}
                  {info.release_url ? (
                    <a
                      className="underline"
                      href={info.release_url as string}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Release notes
                    </a>
                  ) : null}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">You are on the latest version.</p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={async () => {
                    try {
                      await apiClient.POST("/api/v1/system/updates/check", {} as never);
                      await qc.invalidateQueries({ queryKey: ["system", "updates"] });
                      toast.success("Update check complete");
                    } catch {
                      toast.error("Update check failed");
                    }
                  }}
                >
                  Check now
                </Button>
                {info.apply_supported && info.update_available ? (
                  <Button
                    onClick={async () => {
                      if (
                        !confirm(
                          "Pull the latest image and restart the container? The app will be briefly unavailable.",
                        )
                      )
                        return;
                      try {
                        const { error } = await apiClient.POST("/api/v1/system/updates/apply", {
                          body: { confirm: true } as never,
                        });
                        if (error) {
                          toast.error("Apply rejected");
                          return;
                        }
                        toast.success("Apply triggered");
                        await qc.invalidateQueries({
                          queryKey: ["system", "updates", "status"],
                        });
                      } catch {
                        toast.error("Apply failed");
                      }
                    }}
                  >
                    Apply update
                  </Button>
                ) : null}
              </div>
              {status && status.state && status.state !== "idle" ? (
                <div className="space-y-1 rounded-md border px-3 py-2 text-sm">
                  <p>
                    <span className="text-muted-foreground">Apply state: </span>
                    <span className="font-mono">{status.state as string}</span>
                  </p>
                  {status.error ? (
                    <p className="text-destructive">{status.error as string}</p>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Update Settings</CardTitle>
          <CardDescription>
            How often we poll the upstream repo and whether updates can be applied from the UI.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5 pr-4">
              <Label className="text-sm font-medium">
                Check for updates
                <OverrideMarker path={["updates", "enabled"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                When off, no background checks run and the status panel is static.
              </p>
            </div>
            <Switch
              checked={Boolean(upd.enabled ?? true)}
              onCheckedChange={(v) => setUpdatesPath(["enabled"], v)}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                GitHub repo
                <OverrideMarker path={["updates", "repo"]} />
              </Label>
              <Input
                value={String(upd.repo ?? "")}
                onChange={(e) => setUpdatesPath(["repo"], e.target.value)}
                placeholder="owner/name"
              />
            </div>
            <div className="space-y-2">
              <Label>
                Check interval (hours)
                <OverrideMarker path={["updates", "check_interval_hours"]} />
              </Label>
              <Input
                type="number"
                min={1}
                value={Number(upd.check_interval_hours ?? "") || ""}
                onChange={(e) =>
                  setUpdatesPath(["check_interval_hours"], Number(e.target.value) || 0)
                }
              />
            </div>
            <div className="space-y-2">
              <Label>
                Cache TTL (seconds)
                <OverrideMarker path={["updates", "cache_ttl_seconds"]} />
              </Label>
              <Input
                type="number"
                min={0}
                value={Number(upd.cache_ttl_seconds ?? "") || ""}
                onChange={(e) => setUpdatesPath(["cache_ttl_seconds"], Number(e.target.value) || 0)}
              />
              <p className="text-xs text-muted-foreground">
                How long the upstream release lookup is cached before refetch.
              </p>
            </div>
          </div>

          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5 pr-4">
              <Label className="text-sm font-medium">
                Include pre-releases
                <OverrideMarker path={["updates", "include_prereleases"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Surface beta/RC tags instead of only stable releases.
              </p>
            </div>
            <Switch
              checked={Boolean(upd.include_prereleases)}
              onCheckedChange={(v) => setUpdatesPath(["include_prereleases"], v)}
            />
          </div>

          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5 pr-4">
              <Label className="text-sm font-medium">
                Notify on new version
                <OverrideMarker path={["updates", "notify_on_new_version"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Send a notification through your configured channels when a newer version is
                detected.
              </p>
            </div>
            <Switch
              checked={Boolean(upd.notify_on_new_version)}
              onCheckedChange={(v) => setUpdatesPath(["notify_on_new_version"], v)}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>In-App Apply (Docker)</CardTitle>
          <CardDescription>
            Pull a new image and restart the container without leaving the UI. Requires the Docker
            socket to be mounted into this container.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5 pr-4">
              <Label className="text-sm font-medium">
                Allow in-app apply
                <OverrideMarker path={["updates", "allow_in_app_apply"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Off by default. Only enable on hosts that mount{" "}
                <code className="rounded bg-muted px-1 py-0.5 text-[0.7rem]">
                  /var/run/docker.sock
                </code>
                .
              </p>
            </div>
            <Switch
              checked={Boolean(upd.allow_in_app_apply)}
              onCheckedChange={(v) => setUpdatesPath(["allow_in_app_apply"], v)}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                Image repository
                <OverrideMarker path={["updates", "image_repository"]} />
              </Label>
              <Input
                value={String(upd.image_repository ?? "")}
                onChange={(e) => setUpdatesPath(["image_repository"], e.target.value)}
                placeholder="ghcr.io/owner/name"
              />
            </div>
            <div className="space-y-2">
              <Label>
                Image tag
                <OverrideMarker path={["updates", "image_tag"]} />
              </Label>
              <Input
                value={String(upd.image_tag ?? "")}
                onChange={(e) => setUpdatesPath(["image_tag"], e.target.value)}
                placeholder="latest"
              />
            </div>
            <div className="space-y-2">
              <Label>
                Container name
                <OverrideMarker path={["updates", "container_name"]} />
              </Label>
              <Input
                value={String(upd.container_name ?? "")}
                onChange={(e) => setUpdatesPath(["container_name"], e.target.value)}
                placeholder="miramedia"
              />
              <p className="text-xs text-muted-foreground">
                The Docker container to recreate on apply.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

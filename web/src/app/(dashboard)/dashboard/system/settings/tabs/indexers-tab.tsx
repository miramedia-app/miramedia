"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { SecretInput } from "@/components/ui/secret-input";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

export function IndexersTab({
  indexers,
  setIndexersPath,
  misc,
  setMiscPath,
}: {
  indexers: AnyObj;
  setIndexersPath: SetPath;
  misc: AnyObj;
  setMiscPath: SetPath;
}) {
  const ind = indexers;
  const m = misc;
  const native = (ind.native as AnyObj | undefined) ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Indexer Settings</CardTitle>
          <CardDescription>Shared settings applied to all indexer providers.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>
              Search result cache retention (days)
              <OverrideMarker path={["misc", "indexer_query_result_retention_days"]} />
            </Label>
            <Input
              type="number"
              min={1}
              value={Number(m.indexer_query_result_retention_days ?? "") || ""}
              onChange={(e) =>
                setMiscPath(["indexer_query_result_retention_days"], Number(e.target.value) || 0)
              }
              placeholder="7"
              className="max-w-[120px]"
            />
            <p className="text-xs text-muted-foreground">
              Purges stale rows from the indexer query cache nightly. Lower values save database
              space; search re-fetches from indexers when needed.
            </p>
          </div>
          <div className="space-y-2">
            <Label>
              Timeout (seconds)
              <OverrideMarker path={["indexers", "timeout_seconds"]} />
            </Label>
            <Input
              type="number"
              min={1}
              value={Number(ind.timeout_seconds ?? "") || ""}
              onChange={(e) => setIndexersPath(["timeout_seconds"], Number(e.target.value) || 0)}
              placeholder="60"
              className="max-w-[120px]"
            />
            <p className="text-xs text-muted-foreground">
              HTTP timeout shared by Prowlarr, Jackett, and the native indexer.
            </p>
          </div>
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Native Indexers</CardTitle>
              <Switch
                checked={Boolean(native.enabled)}
                onCheckedChange={(v) => setIndexersPath(["native", "enabled"], v)}
              />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Max Concurrent Searches</Label>
              <Input
                type="number"
                value={Number(native.max_concurrent_searches ?? "") || ""}
                onChange={(e) =>
                  setIndexersPath(
                    ["native", "max_concurrent_searches"],
                    Number(e.target.value) || 0,
                  )
                }
                className="max-w-[120px]"
              />
            </div>
          </CardContent>
        </Card>

        {(["prowlarr", "jackett"] as const).map((provider) => {
          const cfg = (ind[provider] as AnyObj | undefined) ?? {};
          return (
            <Card key={provider}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>{provider === "prowlarr" ? "Prowlarr" : "Jackett"}</CardTitle>
                  <Switch
                    checked={Boolean(cfg.enabled)}
                    onCheckedChange={(v) => setIndexersPath([provider, "enabled"], v)}
                  />
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label>URL</Label>
                    <Input
                      value={String(cfg.url ?? "")}
                      onChange={(e) => setIndexersPath([provider, "url"], e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>API Key</Label>
                    <SecretInput
                      value={String(cfg.api_key ?? "")}
                      onValueChange={(v) => setIndexersPath([provider, "api_key"], v)}
                    />
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

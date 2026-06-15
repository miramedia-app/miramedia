"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { SecretInput } from "@/components/ui/secret-input";
import { TestButton } from "@/components/ui/test-button";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

export function RequestsTab({
  requests,
  setRequestsPath,
}: {
  requests: AnyObj;
  setRequestsPath: SetPath;
}) {
  const req = requests;
  const native = (req.native as AnyObj | undefined) ?? {};
  const seerr = (req.seerr as AnyObj | undefined) ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Request Settings</CardTitle>
          <CardDescription>
            Shared options for media requests. Requests are active whenever any backend below
            (Native or Seerr) is enabled.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <Label>Auto-approve user requests</Label>
            <Switch
              checked={Boolean(req.auto_approve_users)}
              onCheckedChange={(v) => setRequestsPath(["auto_approve_users"], v)}
            />
          </div>
          <div className="space-y-2">
            <Label>
              Fulfill Interval (hours)
              <OverrideMarker path={["requests", "fulfill_interval_hours"]} />
            </Label>
            <Input
              type="number"
              min={1}
              value={Number(req.fulfill_interval_hours ?? "") || ""}
              onChange={(e) =>
                setRequestsPath(["fulfill_interval_hours"], Number(e.target.value) || 0)
              }
              placeholder="2"
              className="max-w-[120px]"
            />
            <p className="text-xs text-muted-foreground">
              How often the scheduler tries to fulfill pending requests.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Native Requests</CardTitle>
              <CardDescription>
                Built-in fulfillment via your configured indexers and torrent clients. No external
                service required.
              </CardDescription>
            </div>
            <Switch
              checked={Boolean(native.enabled ?? true)}
              onCheckedChange={(v) => setRequestsPath(["native", "enabled"], v)}
            />
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Seerr Integration</CardTitle>
              <CardDescription>
                Forward requests to an existing Overseerr or Jellyseerr instance.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <TestButton integration="seerr" getConfig={() => seerr} />
              <Switch
                checked={Boolean(seerr.enabled)}
                onCheckedChange={(v) => setRequestsPath(["seerr", "enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>URL</Label>
              <Input
                value={String(seerr.url ?? "")}
                onChange={(e) => setRequestsPath(["seerr", "url"], e.target.value)}
                placeholder="https://seerr.example.com"
              />
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <SecretInput
                value={String(seerr.api_key ?? "")}
                onValueChange={(v) => setRequestsPath(["seerr", "api_key"], v)}
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

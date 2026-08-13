"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { SecretInput } from "@/components/ui/secret-input";
import { TestButton } from "@/components/ui/test-button";
import { LanguageMultiCombobox } from "@/components/ui/language-multi-combobox";
import { OverrideMarker } from "../_marker";
import { csvToArray, type AnyObj, type SetPath } from "../_shared";

export function MetadataTab({
  metadata,
  setMetadataPath,
}: {
  metadata: AnyObj;
  setMetadataPath: SetPath;
}) {
  const meta = metadata;
  const native = (meta.native as AnyObj | undefined) ?? {};
  const tvmaze = (native.tvmaze as AnyObj | undefined) ?? {};
  const cinemeta = (native.cinemeta as AnyObj | undefined) ?? {};
  const tmdb = (meta.tmdb as AnyObj | undefined) ?? {};
  const tvdb = (meta.tvdb as AnyObj | undefined) ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Metadata Settings</CardTitle>
          <CardDescription>Language filter and refresh interval for all providers.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>
              Desired Languages
              <OverrideMarker path={["metadata", "desired_languages"]} />
            </Label>
            <LanguageMultiCombobox
              value={
                Array.isArray(meta.desired_languages) ? (meta.desired_languages as string[]) : []
              }
              onChange={(codes) => setMetadataPath(["desired_languages"], codes)}
              placeholder="Add a language"
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                Check Interval (hours)
                <OverrideMarker path={["metadata", "check_interval_hours"]} />
              </Label>
              <Input
                type="number"
                min={1}
                value={Number(meta.check_interval_hours ?? "") || ""}
                onChange={(e) =>
                  setMetadataPath(["check_interval_hours"], Number(e.target.value) || 0)
                }
                placeholder="24"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Native Metadata</CardTitle>
          <CardDescription>Providers used to fetch show and movie metadata.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">TVmaze</Label>
              <p className="text-xs text-muted-foreground">
                Free TV show metadata source. No API key required.
              </p>
            </div>
            <Switch
              checked={Boolean(tvmaze.enabled ?? true)}
              onCheckedChange={(v) => setMetadataPath(["native", "tvmaze", "enabled"], v)}
            />
          </div>

          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label className="text-sm font-medium">Cinemeta</Label>
              <p className="text-xs text-muted-foreground">
                Free movie metadata + trending source. No API key required.
              </p>
            </div>
            <Switch
              checked={Boolean(cinemeta.enabled ?? true)}
              onCheckedChange={(v) => setMetadataPath(["native", "cinemeta", "enabled"], v)}
            />
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">TMDB</Label>
                <p className="text-xs text-muted-foreground">
                  The Movie Database. Requires a free API key from{" "}
                  <a
                    href="https://www.themoviedb.org/settings/api"
                    target="_blank"
                    className="underline"
                    rel="noreferrer"
                  >
                    themoviedb.org
                  </a>
                  .
                </p>
              </div>
              <div className="flex items-center gap-2">
                <TestButton integration="tmdb" getConfig={() => tmdb} />
                <Switch
                  checked={Boolean(tmdb.enabled)}
                  onCheckedChange={(v) => setMetadataPath(["tmdb", "enabled"], v)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <SecretInput
                value={String(tmdb.api_key ?? "")}
                onValueChange={(v) => setMetadataPath(["tmdb", "api_key"], v)}
                placeholder="Enter your TMDB API key"
              />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>Default Language</Label>
                <Input
                  value={String(tmdb.default_language ?? "")}
                  onChange={(e) => setMetadataPath(["tmdb", "default_language"], e.target.value)}
                  placeholder="en"
                />
              </div>
              <div className="space-y-2">
                <Label>Primary Languages (comma-separated)</Label>
                <Input
                  value={
                    Array.isArray(tmdb.primary_languages)
                      ? (tmdb.primary_languages as string[]).join(", ")
                      : ""
                  }
                  onChange={(e) =>
                    setMetadataPath(["tmdb", "primary_languages"], csvToArray(e.target.value))
                  }
                  placeholder="e.g. no, sv"
                />
              </div>
            </div>
          </div>

          <div className="space-y-3 rounded-md border px-4 py-3">
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label className="text-sm font-medium">TVDB</Label>
                <p className="text-xs text-muted-foreground">
                  TheTVDB. Requires an API key from{" "}
                  <a
                    href="https://thetvdb.com/api-information"
                    target="_blank"
                    className="underline"
                    rel="noreferrer"
                  >
                    thetvdb.com
                  </a>
                  .
                </p>
              </div>
              <div className="flex items-center gap-2">
                <TestButton integration="tvdb" getConfig={() => tvdb} />
                <Switch
                  checked={Boolean(tvdb.enabled)}
                  onCheckedChange={(v) => setMetadataPath(["tvdb", "enabled"], v)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <SecretInput
                value={String(tvdb.api_key ?? "")}
                onValueChange={(v) => setMetadataPath(["tvdb", "api_key"], v)}
                placeholder="Enter your TVDB API key"
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { SecretInput } from "@/components/ui/secret-input";
import { TestButton } from "@/components/ui/test-button";
import { LanguageMultiCombobox } from "@/components/ui/language-multi-combobox";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

const FREE_PROVIDERS = [
  [
    "embeddedsubtitles",
    "Embedded Subtitles",
    "Extracts subtitle tracks already inside the video file.",
  ],
  ["gestdown", "Gestdown", "Free TV subtitle provider."],
  ["tvsubtitles", "TVSubtitles", "Free TV subtitle provider."],
  ["yifysubtitles", "YIFY Subtitles", "Movie subtitles. Complements YTS indexer."],
  ["subtitlecat", "SubtitleCat", "Broad multi-language scraper. Movies and TV."],
  ["subf2m", "Subf2m (Subscene)", "Subscene successor. Large catalog, movies and TV."],
  ["isubtitles", "iSubtitles", "Multi-language scraper. Movies and TV."],
  ["my_subs", "My-Subs", "Multi-language scraper. Movies and TV."],
] as const;

const USERPASS_PROVIDERS = [
  [
    "opensubtitlescom",
    "OpenSubtitles.com",
    "Best coverage for movies and TV. Requires a free account.",
    true,
  ],
  ["addic7ed", "Addic7ed", "TV subtitles. Requires account.", false],
] as const;

const APIKEY_PROVIDERS = [
  [
    "subdl",
    "SubDL",
    "Excellent coverage for movies and TV. Requires a free API key from subdl.com.",
  ],
  [
    "subsource",
    "Subsource",
    "Large database, movies and TV. Requires a free API key from your subsource.net profile.",
  ],
] as const;

const LANG_PROVIDERS = [
  ["napiprojekt", "NapiProjekt", "Polish subtitles."],
  ["subtis", "Subtis", "Spanish movie subtitles."],
  ["subtitulamos", "Subtitulamos", "Spanish and Portuguese TV subtitles."],
] as const;

export function SubtitlesTab({
  subtitles,
  setSubtitlesPath,
}: {
  subtitles: AnyObj;
  setSubtitlesPath: SetPath;
}) {
  const sub = subtitles;
  const subNative = (sub.native ?? {}) as AnyObj;
  const bazarr = (sub.bazarr as AnyObj | undefined) ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Subtitle Settings</CardTitle>
          <CardDescription>
            Shared options for subtitles. Subtitles are active whenever any backend below (Native or
            Bazarr) is enabled.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>
              Desired Languages
              <OverrideMarker path={["subtitles", "desired_languages"]} />
            </Label>
            <LanguageMultiCombobox
              value={
                Array.isArray(sub.desired_languages) ? (sub.desired_languages as string[]) : []
              }
              onChange={(codes) => setSubtitlesPath(["desired_languages"], codes)}
              placeholder="Add a language"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Native Subtitles</CardTitle>
              <CardDescription>
                Search and download subtitles automatically using subliminal
              </CardDescription>
            </div>
            <Switch
              checked={Boolean(subNative.enabled)}
              onCheckedChange={(v) => setSubtitlesPath(["native", "enabled"], v)}
            />
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Scan Interval (hours)</Label>
            <Input
              type="number"
              value={Number(subNative.scan_interval_hours ?? "") || ""}
              onChange={(e) =>
                setSubtitlesPath(["native", "scan_interval_hours"], Number(e.target.value) || 0)
              }
            />
          </div>
          <Separator />

          <section className="space-y-2">
            <h4 className="text-sm font-semibold">Free Providers</h4>
            {FREE_PROVIDERS.map(([k, label, desc]) => (
              <div
                key={k}
                className="flex items-center justify-between rounded-md border px-4 py-3"
              >
                <div className="space-y-0.5">
                  <Label className="text-sm font-medium">{label}</Label>
                  <p className="text-xs text-muted-foreground">{desc}</p>
                </div>
                <Switch
                  checked={Boolean((subNative[k] as AnyObj | undefined)?.enabled ?? true)}
                  onCheckedChange={(v) => setSubtitlesPath(["native", k, "enabled"], v)}
                />
              </div>
            ))}
          </section>

          <section className="space-y-2">
            <h4 className="text-sm font-semibold">Account-Based Providers</h4>

            {USERPASS_PROVIDERS.map(([k, label, desc, recommended]) => {
              const cfg = (subNative[k] as AnyObj | undefined) ?? {};
              return (
                <div key={k} className="space-y-3 rounded-md border px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <Label className="text-sm font-medium">{label}</Label>
                        {recommended && (
                          <Badge variant="outline" className="text-xs">
                            Recommended
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">{desc}</p>
                    </div>
                    <Switch
                      checked={Boolean(cfg.enabled)}
                      onCheckedChange={(v) => setSubtitlesPath(["native", k, "enabled"], v)}
                    />
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label>Username</Label>
                      <Input
                        value={String(cfg.username ?? "")}
                        onChange={(e) =>
                          setSubtitlesPath(["native", k, "username"], e.target.value)
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Password</Label>
                      <SecretInput
                        value={String(cfg.password ?? "")}
                        onValueChange={(v) => setSubtitlesPath(["native", k, "password"], v)}
                      />
                    </div>
                  </div>
                </div>
              );
            })}

            {APIKEY_PROVIDERS.map(([k, label, desc]) => {
              const cfg = (subNative[k] as AnyObj | undefined) ?? {};
              return (
                <div key={k} className="space-y-3 rounded-md border px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label className="text-sm font-medium">{label}</Label>
                      <p className="text-xs text-muted-foreground">{desc}</p>
                    </div>
                    <Switch
                      checked={Boolean(cfg.enabled)}
                      onCheckedChange={(v) => setSubtitlesPath(["native", k, "enabled"], v)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>API Key</Label>
                    <SecretInput
                      value={String(cfg.api_key ?? "")}
                      onValueChange={(v) => setSubtitlesPath(["native", k, "api_key"], v)}
                    />
                  </div>
                </div>
              );
            })}
          </section>

          <section className="space-y-2">
            <h4 className="text-sm font-semibold">Language-Specific Providers</h4>
            {LANG_PROVIDERS.map(([k, label, desc]) => (
              <div
                key={k}
                className="flex items-center justify-between rounded-md border px-4 py-3"
              >
                <div className="space-y-0.5">
                  <Label className="text-sm font-medium">{label}</Label>
                  <p className="text-xs text-muted-foreground">{desc}</p>
                </div>
                <Switch
                  checked={Boolean((subNative[k] as AnyObj | undefined)?.enabled)}
                  onCheckedChange={(v) => setSubtitlesPath(["native", k, "enabled"], v)}
                />
              </div>
            ))}
          </section>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Bazarr Integration</CardTitle>
              <CardDescription>
                Use an external Bazarr instance for subtitle management
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <TestButton integration="bazarr" getConfig={() => bazarr} />
              <Switch
                checked={Boolean(bazarr.enabled)}
                onCheckedChange={(v) => setSubtitlesPath(["bazarr", "enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>URL</Label>
              <Input
                value={String(bazarr.url ?? "")}
                onChange={(e) => setSubtitlesPath(["bazarr", "url"], e.target.value)}
                placeholder="http://localhost:6767"
              />
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <SecretInput
                value={String(bazarr.api_key ?? "")}
                onValueChange={(v) => setSubtitlesPath(["bazarr", "api_key"], v)}
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import * as React from "react";
import { Copy, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { SecretInput } from "@/components/ui/secret-input";
import { TestButton } from "@/components/ui/test-button";
import { LanguageMultiCombobox } from "@/components/ui/language-multi-combobox";
import { copyToClipboard } from "@/lib/utils";
import { OverrideMarker } from "../_marker";
import type { AnyObj, SetPath } from "../_shared";

// 32 hex chars, the same shape Sonarr/Radarr keys take so Bazarr's field
// validation is happy. crypto.getRandomValues is available in every browser
// context, including plain HTTP (unlike crypto.randomUUID and crypto.subtle,
// which are secure-context only) — so there is no Math.random fallback here:
// a credential must never come from a non-cryptographic source.
function newShimApiKey(): string | null {
  if (typeof crypto === "undefined" || typeof crypto.getRandomValues !== "function") {
    return null;
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

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

function ShimUrlRow({ label, url }: { label: string; url: string }) {
  async function copy() {
    try {
      await copyToClipboard(url);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Clipboard access denied");
    }
  }
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <div className="flex min-w-0 items-center gap-1">
        <code className="truncate rounded bg-muted px-1 py-0.5 font-mono text-xs">{url}</code>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0"
          aria-label={`Copy ${label} URL`}
          title="Copy"
          onClick={copy}
        >
          <Copy className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

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
  // Static export prerenders this page with no window, so the origin has to be
  // read after mount or the markup mismatches on hydration.
  const [origin, setOrigin] = React.useState("");
  React.useEffect(() => setOrigin(window.location.origin), []);
  const shownOrigin = origin || "http://<miramedia-host>:8000";
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
          <div className="grid gap-4 md:grid-cols-2">
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

          <Separator />

          <section className="space-y-4">
            <div className="space-y-1">
              <h4 className="text-sm font-semibold">Sonarr/Radarr Shim</h4>
              <p className="text-xs text-muted-foreground">
                MiraMedia serves its library to Bazarr through a read-only Sonarr and Radarr
                compatibility API. Set a shim key here, then add MiraMedia to Bazarr as if it were
                Sonarr and Radarr.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="bazarr-shim-api-key">
                Shim API Key
                <OverrideMarker path={["subtitles", "bazarr", "shim_api_key"]} />
              </Label>
              <div className="flex items-center gap-2">
                <SecretInput
                  id="bazarr-shim-api-key"
                  className="flex-1"
                  value={String(bazarr.shim_api_key ?? "")}
                  onValueChange={(v) => setSubtitlesPath(["bazarr", "shim_api_key"], v)}
                />
                <Button
                  type="button"
                  variant="outline"
                  className="shrink-0"
                  onClick={() => {
                    const key = newShimApiKey();
                    if (key === null) {
                      toast.error("This browser cannot generate a secure key");
                      return;
                    }
                    setSubtitlesPath(["bazarr", "shim_api_key"], key);
                  }}
                >
                  <RefreshCw className="mr-1 h-4 w-4" />
                  Generate
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Bazarr must send this key to reach the shim. Until it is set, the shim rejects every
                request with 401. Generating a new key breaks any Bazarr instance still using the
                old one.
              </p>
            </div>

            <div className="space-y-3 rounded-md border bg-muted/40 px-4 py-3">
              <p className="text-xs font-medium text-muted-foreground">Set up in Bazarr</p>
              <div className="space-y-2">
                <ShimUrlRow label="Sonarr URL" url={`${shownOrigin}/sonarr`} />
                <ShimUrlRow label="Radarr URL" url={`${shownOrigin}/radarr`} />
              </div>
              <p className="text-xs text-muted-foreground">
                In Bazarr, go to Settings → Sonarr and add a Sonarr with the URL above, then
                Settings → Radarr and add a Radarr with its URL. For both, use the shim API key as
                the API key. Bazarr will then list your shows and movies and download subtitles for
                them.
              </p>
              <p className="text-xs text-muted-foreground">
                Bazarr must see your media at the same paths MiraMedia uses — mount your library
                into the Bazarr container at the identical path, or configure path mappings in
                Bazarr&apos;s Sonarr and Radarr settings. Otherwise Bazarr finds no files and writes
                subtitles nowhere useful.
              </p>
            </div>
          </section>
        </CardContent>
      </Card>
    </div>
  );
}

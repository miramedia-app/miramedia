"use client";

import * as React from "react";
import { Plus, Trash2, CheckIcon, ChevronsUpDownIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { OverrideMarker } from "../_marker";
import { csvToArray, newRowKey, type AnyObj, type Keyed, type SetPath } from "../_shared";

// IANA zones straight from the browser's Intl DB — no bundled list to age out.
// Guarded because Intl.supportedValuesOf is not in every runtime's typings.
const supportedTimeZones = (
  Intl as typeof Intl & { supportedValuesOf?: (key: "timeZone") => string[] }
).supportedValuesOf;
const TIMEZONES: string[] =
  typeof supportedTimeZones === "function" ? supportedTimeZones("timeZone") : [];

const SERVER_DEFAULT_LABEL = "Server Default";

/** Searchable IANA timezone picker. Empty value = use the server's zone. */
function TimezoneCombobox({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const listboxId = React.useId();
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            className="w-full justify-between font-normal"
            role="combobox"
            aria-expanded={open}
            aria-controls={listboxId}
          />
        }
      >
        <span className={cn("truncate", !value && "text-muted-foreground")}>
          {value || SERVER_DEFAULT_LABEL}
        </span>
        <ChevronsUpDownIcon className="opacity-50" />
      </PopoverTrigger>
      <PopoverContent className="p-0" align="start">
        <Command id={listboxId}>
          <CommandInput placeholder="Search timezone..." />
          <CommandList>
            <CommandEmpty>No timezone found.</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value={SERVER_DEFAULT_LABEL}
                onSelect={() => {
                  onChange("");
                  setOpen(false);
                }}
              >
                <CheckIcon className={cn("mr-2", value === "" ? "opacity-100" : "opacity-0")} />
                {SERVER_DEFAULT_LABEL}
              </CommandItem>
              {TIMEZONES.map((tz) => (
                <CommandItem
                  key={tz}
                  value={tz}
                  onSelect={() => {
                    onChange(tz);
                    setOpen(false);
                  }}
                >
                  <CheckIcon className={cn("mr-2", value === tz ? "opacity-100" : "opacity-0")} />
                  {tz}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

const CF_SOLVERS = [
  [
    "native",
    "Native",
    "Runs Chromium on this host. Default — but a GPU-less NAS reports SwiftShader WebGL and gets flagged almost everywhere.",
  ],
  [
    "remote",
    "Remote",
    "Attach to a Chrome you run on a machine with a real GPU. Best free fix: real WebGL fingerprint = looks like a real device.",
  ],
  [
    "byparr",
    "Byparr",
    "Camoufox-based sidecar container. Highest open-source Turnstile success rate in 2026.",
  ],
  ["flaresolverr", "FlareSolverr", "Classic undetected-chromedriver sidecar container."],
  [
    "browser_run",
    "Browser Run",
    "Hosted real Chrome in Cloudflare's infra. Free tier: 10 min/day, 3 concurrent.",
  ],
  [
    "firecrawl",
    "Firecrawl",
    "Managed scrape API with built-in stealth. Free tier: 500 lifetime credits.",
  ],
] as const;

const STORAGE_PATHS = [
  ["image_directory", "Image Directory"],
  ["torrent_directory", "Torrents Directory"],
  ["movie_directory", "Movie Directory"],
  ["show_directory", "Show Directory"],
] as const;

const FIRST_NAMING = [
  ["show_folder_format", "Show folder format", "{title} ({year}) {provider_tag}"],
  ["season_folder_format", "Season folder format", "Season {season_number}"],
] as const;

const LAST_NAMING = [
  ["movie_folder_format", "Movie folder format", "{title} ({year}) {provider_tag}"],
  ["movie_file_format", "Movie filename format", "{title} ({year}){suffix}"],
] as const;

const TOKEN_GROUPS = [
  {
    heading: "Show & movie folders",
    scope: "show_folder_format, movie_folder_format",
    tokens: [
      [
        "{title}",
        "Media title. Use {movie_title} or {show_title} as aliases inside their file format.",
      ],
      ["{year}", "Release year. Empty for shows without a known premiere year."],
      ["{imdb_id}", "Raw IMDb ID (tt1234567). Empty if not resolved."],
      ["{provider}", "Metadata source that supplied this item: imdb, tmdb, or tvdb."],
      ["{provider_id}", "That provider's own ID for the item (e.g. 12345), stringified."],
      [
        "{provider_tag}",
        "Preferred bracketed IMDb tag, e.g. [imdb-tt1234567]. Falls back to [{provider}id-{provider_id}] when IMDb is missing.",
      ],
    ],
  },
  {
    heading: "Season folder",
    scope: "season_folder_format",
    tokens: [
      ["{season_number}", "Season number as integer (1, 2, 10)."],
      [
        "{season_number_00}",
        "Zero-padded 2-digit season (01, 02, 10). Equivalent to {season_number:02d}.",
      ],
    ],
  },
  {
    heading: "Episode filename",
    scope: "episode_file_format",
    tokens: [
      ["{show_title}", "Show title."],
      ["{season_number}", "Season as integer."],
      ["{season_number_00}", "Zero-padded 2-digit season."],
      ["{episode_number}", "Episode as integer."],
      ["{episode_number_00}", "Zero-padded 2-digit episode."],
      ["{year}", "Show premiere year."],
      ["{provider_tag}", "Bracketed provider match ID (see folder tokens above)."],
      ["{quality}", "Rendered quality label, e.g. 1080p, 4K. Empty for unknown."],
      ["{variant}", "User-entered variant (e.g. director-cut). Empty when none."],
      ["{codec}", "Normalised video codec (h264, h265, av1, ...). Empty when unknown."],
      ["{hdr}", "Literal 'hdr' when the file is HDR, empty otherwise."],
      ["{source}", "Normalised source (bluray, web, remux, hdtv, ...). Empty when unknown."],
      ["{extra}", "Auto collision discriminator (e.g. '2', '3'). Empty in normal cases."],
      [
        "{suffix}",
        "Pre-formatted ' - 1080p [h265-director-cut]' style suffix combining quality + codec + variant + extra. Empty when none.",
      ],
    ],
  },
  {
    heading: "Movie filename",
    scope: "movie_file_format",
    tokens: [
      ["{title} / {movie_title}", "Movie title."],
      ["{year}", "Release year."],
      ["{provider_tag}", "Bracketed provider match ID (see folder tokens above)."],
      ["{quality}", "Rendered quality label."],
      ["{variant}", "User-entered variant (e.g. director-cut)."],
      ["{codec}", "Normalised video codec (h264, h265, av1, ...)."],
      ["{hdr}", "Literal 'hdr' or empty."],
      ["{source}", "Normalised source (bluray, web, remux, ...)."],
      ["{extra}", "Auto collision discriminator (e.g. '2', '3')."],
      ["{suffix}", "Pre-formatted quality/codec/variant/extra suffix."],
    ],
  },
] as const;

export function GeneralTab({
  misc,
  setMiscPath,
  cloudflare,
  setCloudflarePath,
}: {
  misc: AnyObj;
  setMiscPath: SetPath;
  cloudflare: AnyObj;
  setCloudflarePath: SetPath;
}) {
  const m = misc;
  const naming = (m.naming ?? {}) as AnyObj;
  const cf = cloudflare;
  const solver = String(cf.solver ?? "native") || "native";
  const isExternal = ["byparr", "flaresolverr", "browser_run", "firecrawl"].includes(solver);
  const remote = (cf.remote ?? {}) as AnyObj;
  const byparr = (cf.byparr ?? {}) as AnyObj;
  const flaresolverr = (cf.flaresolverr ?? {}) as AnyObj;
  const browserRun = (cf.browser_run ?? {}) as AnyObj;
  const firecrawl = (cf.firecrawl ?? {}) as AnyObj;
  const cfNum = (k: string, d: number) => Number(cf[k] ?? d) || d;
  const cfLaunch = cfNum("browser_launch_timeout_seconds", 240);
  const cfPageLoad = cfNum("page_load_timeout_seconds", 150);
  const cfChallenge = cfNum("challenge_wait_seconds", 10);
  const cfSolve = cfNum("solve_timeout_seconds", 180);
  const cfTotal = cfLaunch + cfPageLoad + cfChallenge + cfSolve;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>General Settings</CardTitle>
          <CardDescription>
            App-wide configuration: public URL, allowed origins, retention, and developer mode.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                Frontend URL
                <OverrideMarker path={["misc", "frontend_url"]} />
              </Label>
              <Input
                value={String(m.frontend_url ?? "")}
                onChange={(e) => setMiscPath(["frontend_url"], e.target.value)}
                placeholder="https://miramedia.example.com"
              />
              <p className="text-xs text-muted-foreground">
                Public URL the frontend is served from. Used in notification links and OIDC
                redirects.
              </p>
            </div>
            <div className="space-y-2">
              <Label>
                CORS URLs (comma-separated)
                <OverrideMarker path={["misc", "cors_urls"]} />
              </Label>
              <Input
                value={Array.isArray(m.cors_urls) ? (m.cors_urls as string[]).join(", ") : ""}
                onChange={(e) => setMiscPath(["cors_urls"], csvToArray(e.target.value))}
                placeholder="http://localhost:5555"
              />
              <p className="text-xs text-muted-foreground">
                Browser origins allowed to call the API. Add LAN/remote URLs you load the UI from.
              </p>
            </div>
            <div className="space-y-2">
              <Label>
                Timezone
                <OverrideMarker path={["misc", "timezone"]} />
              </Label>
              <TimezoneCombobox
                value={String(m.timezone ?? "")}
                onChange={(v) => setMiscPath(["timezone"], v)}
              />
              <p className="text-xs text-muted-foreground">
                Timezone used for episode/movie air dates.
              </p>
            </div>
            <div className="space-y-2">
              <Label>
                Log Retention (days)
                <OverrideMarker path={["misc", "log_retention_days"]} />
              </Label>
              <Input
                type="number"
                min={1}
                value={Number(m.log_retention_days ?? "") || ""}
                onChange={(e) => setMiscPath(["log_retention_days"], Number(e.target.value) || 0)}
                placeholder="30"
              />
              <p className="text-xs text-muted-foreground">
                Activity log entries older than this are auto-deleted daily.
              </p>
            </div>
          </div>
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div className="space-y-0.5">
              <Label>
                Development mode
                <OverrideMarker path={["misc", "development"]} />
              </Label>
              <p className="text-xs text-muted-foreground">
                Verbose logging and dev-only conveniences. Leave off in production.
              </p>
            </div>
            <Switch
              checked={Boolean(m.development)}
              onCheckedChange={(v) => setMiscPath(["development"], v)}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex min-w-0 items-start justify-between gap-4">
            <div className="min-w-0 space-y-1.5">
              <CardTitle>Cloudflare Bypass</CardTitle>
              <CardDescription>
                Shared by indexers, subtitle providers, and any future module. When enabled, it
                auto-activates whenever a request hits a Cloudflare challenge response. Pick a{" "}
                <strong>solver backend</strong> below to earn <code>cf_clearance</code> cookies;
                they&apos;re replayed via curl_cffi with a matching browser TLS fingerprint. When
                off, it is never invoked — no chromium, no resources consumed — and protected sites
                fail to fetch.
              </CardDescription>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <OverrideMarker path={["cloudflare", "enabled"]} />
              <Switch
                checked={cf.enabled !== false}
                onCheckedChange={(v) => setCloudflarePath(["enabled"], v)}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Backend provider selector */}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                Backend Provider
                <OverrideMarker path={["cloudflare", "solver"]} />
              </Label>
              <Select value={solver} onValueChange={(v) => setCloudflarePath(["solver"], v)}>
                <SelectTrigger className="w-full">
                  {/* Render the label from the same source as the items so the
                      selected text matches what's shown in the open dropdown. */}
                  {CF_SOLVERS.find(([v]) => v === solver)?.[1] ?? "Native"}
                </SelectTrigger>
                <SelectContent>
                  {CF_SOLVERS.map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {CF_SOLVERS.find(([v]) => v === solver)?.[2]}
              </p>
            </div>
            <div className="space-y-2">
              <Label>
                Proxy
                <OverrideMarker path={["cloudflare", "proxy"]} />
              </Label>
              <Input
                value={String(cf.proxy ?? "")}
                onChange={(e) => setCloudflarePath(["proxy"], e.target.value)}
                placeholder="scheme://[user:pass@]host:port"
              />
              <p className="text-xs text-muted-foreground">
                Optional residential/mobile proxy for curl_cffi replay + the byparr/flaresolverr
                sidecars&apos; own egress. Empty = direct.
              </p>
            </div>
          </div>

          {solver === "remote" && (
            <div className="space-y-2">
              <Label>
                Remote Browser Endpoint
                <OverrideMarker path={["cloudflare", "remote", "endpoint"]} />
              </Label>
              <Input
                value={String(remote.endpoint ?? "")}
                onChange={(e) => setCloudflarePath(["remote", "endpoint"], e.target.value)}
                placeholder="http://host.docker.internal:9222"
              />
              <p className="text-xs text-muted-foreground">
                On a machine with a real GPU, run{" "}
                <code>chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cf</code> (not
                headless, not <code>--disable-gpu</code>) and point here. From a container,{" "}
                <code>host.docker.internal</code> reaches the host; a LAN box is its IP.
              </p>
            </div>
          )}

          {solver === "byparr" && (
            <div className="space-y-2">
              <Label>
                Byparr URL
                <OverrideMarker path={["cloudflare", "byparr", "url"]} />
              </Label>
              <Input
                value={String(byparr.url ?? "")}
                onChange={(e) => setCloudflarePath(["byparr", "url"], e.target.value)}
                placeholder="http://byparr:8191"
              />
              <p className="text-xs text-muted-foreground">
                URL of the Byparr sidecar (a commented service ships in docker-compose.yaml). Run it
                on a real-GPU host for a real fingerprint.
              </p>
            </div>
          )}

          {solver === "flaresolverr" && (
            <div className="space-y-2">
              <Label>
                FlareSolverr URL
                <OverrideMarker path={["cloudflare", "flaresolverr", "url"]} />
              </Label>
              <Input
                value={String(flaresolverr.url ?? "")}
                onChange={(e) => setCloudflarePath(["flaresolverr", "url"], e.target.value)}
                placeholder="http://flaresolverr:8191"
              />
              <p className="text-xs text-muted-foreground">URL of the FlareSolverr sidecar.</p>
            </div>
          )}

          {solver === "browser_run" && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>
                  Cloudflare Account ID
                  <OverrideMarker path={["cloudflare", "browser_run", "account_id"]} />
                </Label>
                <Input
                  value={String(browserRun.account_id ?? "")}
                  onChange={(e) => setCloudflarePath(["browser_run", "account_id"], e.target.value)}
                  placeholder="Cloudflare account id"
                />
              </div>
              <div className="space-y-2">
                <Label>
                  Cloudflare API Token
                  <OverrideMarker path={["cloudflare", "browser_run", "api_token"]} />
                </Label>
                <Input
                  type="password"
                  value={String(browserRun.api_token ?? "")}
                  onChange={(e) => setCloudflarePath(["browser_run", "api_token"], e.target.value)}
                  placeholder="token with Browser Rendering permission"
                />
              </div>
            </div>
          )}

          {solver === "firecrawl" && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>
                  Firecrawl API URL
                  <OverrideMarker path={["cloudflare", "firecrawl", "base_url"]} />
                </Label>
                <Input
                  value={String(firecrawl.base_url ?? "")}
                  onChange={(e) => setCloudflarePath(["firecrawl", "base_url"], e.target.value)}
                  placeholder="https://api.firecrawl.dev"
                />
              </div>
              <div className="space-y-2">
                <Label>
                  Firecrawl API Key
                  <OverrideMarker path={["cloudflare", "firecrawl", "api_key"]} />
                </Label>
                <Input
                  type="password"
                  value={String(firecrawl.api_key ?? "")}
                  onChange={(e) => setCloudflarePath(["firecrawl", "api_key"], e.target.value)}
                  placeholder="fc-..."
                />
              </div>
            </div>
          )}

          <Separator />

          {solver === "native" && (
            <div className="space-y-2">
              <Label>
                Browser Path
                <OverrideMarker path={["cloudflare", "browser_path"]} />
              </Label>
              <Input
                value={String(cf.browser_path ?? "")}
                onChange={(e) => setCloudflarePath(["browser_path"], e.target.value)}
                placeholder="Auto-detect"
              />
              <p className="text-xs text-muted-foreground">
                Path to a Chrome/Chromium binary. Leave empty for auto-detection. (Remote uses the
                browser on the remote host instead.)
              </p>
            </div>
          )}

          {!isExternal && (
            <div className="flex items-center justify-between rounded-md border px-4 py-3">
              <div className="space-y-0.5">
                <Label>
                  Warm up browser on startup
                  <OverrideMarker path={["cloudflare", "warmup_on_startup"]} />
                </Label>
                <p className="text-xs text-muted-foreground">
                  Pre-launch chromium at boot so the first search skips the 5–8s cold-start. Turn
                  off on memory-constrained hosts (idle chromium ≈ 150 MB).
                </p>
              </div>
              <Switch
                checked={cf.warmup_on_startup !== false}
                onCheckedChange={(v) => setCloudflarePath(["warmup_on_startup"], v)}
              />
            </div>
          )}

          {/* Universal — the cookie cache + curl_cffi replay every backend feeds. */}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>
                Cookie TTL (seconds)
                <OverrideMarker path={["cloudflare", "cookie_ttl_seconds"]} />
              </Label>
              <Input
                type="number"
                min={60}
                value={Number(cf.cookie_ttl_seconds ?? "") || ""}
                onChange={(e) =>
                  setCloudflarePath(["cookie_ttl_seconds"], Number(e.target.value) || 0)
                }
                placeholder="900"
              />
              <p className="text-xs text-muted-foreground">
                How long harvested CF sessions stay cached (default 15 min). Used by every backend
                that returns cookies.
              </p>
            </div>
            <div className="space-y-2">
              <Label>
                Impersonation Profile
                <OverrideMarker path={["cloudflare", "impersonate_profile"]} />
              </Label>
              <Input
                value={String(cf.impersonate_profile ?? "")}
                onChange={(e) => setCloudflarePath(["impersonate_profile"], e.target.value)}
                placeholder="chrome131"
              />
              <p className="text-xs text-muted-foreground">
                curl_cffi browser fingerprint used for replay (chrome120, chrome131, safari17,
                firefox133, etc).
              </p>
            </div>
          </div>

          {!isExternal && (
            <div>
              <p className="mb-2 text-sm font-medium">Timeouts (seconds)</p>
              <p className="mb-3 text-xs text-muted-foreground">
                A protected request runs in order: <strong>start browser</strong> (cold start only —
                normally already warm) → <strong>load page</strong> → if challenged,{" "}
                <strong>wait for challenge JS</strong> → <strong>solve &amp; recheck</strong>. Each
                value caps its own phase; the overall request budget is their sum (shown below).
                Increase them on slow NAS hardware.
              </p>
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <div className="space-y-2">
                  <Label>
                    Browser Launch
                    <OverrideMarker path={["cloudflare", "browser_launch_timeout_seconds"]} />
                  </Label>
                  <Input
                    type="number"
                    min={1}
                    value={Number(cf.browser_launch_timeout_seconds ?? "") || ""}
                    onChange={(e) =>
                      setCloudflarePath(
                        ["browser_launch_timeout_seconds"],
                        Number(e.target.value) || 0,
                      )
                    }
                    placeholder="240"
                  />
                  <p className="text-xs text-muted-foreground">
                    Wait for chromium to start up (warmup + first launch).
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>
                    Page Load
                    <OverrideMarker path={["cloudflare", "page_load_timeout_seconds"]} />
                  </Label>
                  <Input
                    type="number"
                    min={1}
                    value={Number(cf.page_load_timeout_seconds ?? "") || ""}
                    onChange={(e) =>
                      setCloudflarePath(["page_load_timeout_seconds"], Number(e.target.value) || 0)
                    }
                    placeholder="150"
                  />
                  <p className="text-xs text-muted-foreground">
                    Single page navigation (challenge page + direct fetch).
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>
                    Challenge Wait
                    <OverrideMarker path={["cloudflare", "challenge_wait_seconds"]} />
                  </Label>
                  <Input
                    type="number"
                    min={0}
                    step="0.5"
                    value={Number(cf.challenge_wait_seconds ?? "") || ""}
                    onChange={(e) =>
                      setCloudflarePath(["challenge_wait_seconds"], Number(e.target.value) || 0)
                    }
                    placeholder="10"
                  />
                  <p className="text-xs text-muted-foreground">
                    Time for Cloudflare challenge JS (Turnstile) to render before solving.
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>
                    Solve
                    <OverrideMarker path={["cloudflare", "solve_timeout_seconds"]} />
                  </Label>
                  <Input
                    type="number"
                    min={1}
                    value={Number(cf.solve_timeout_seconds ?? "") || ""}
                    onChange={(e) =>
                      setCloudflarePath(["solve_timeout_seconds"], Number(e.target.value) || 0)
                    }
                    placeholder="180"
                  />
                  <p className="text-xs text-muted-foreground">
                    How long to keep attempting to solve + check for the cf_clearance cookie.
                  </p>
                </div>
              </div>

              <div className="mt-4 rounded-md border bg-muted/40 px-4 py-3">
                <p className="text-xs font-medium text-muted-foreground">Total request budget</p>
                <p className="mt-1 font-mono text-sm">
                  <span title="Browser launch">{cfLaunch}</span>
                  <span className="text-muted-foreground"> + </span>
                  <span title="Page load">{cfPageLoad}</span>
                  <span className="text-muted-foreground"> + </span>
                  <span title="Challenge wait">{cfChallenge}</span>
                  <span className="text-muted-foreground"> + </span>
                  <span title="Solve">{cfSolve}</span>
                  <span className="text-muted-foreground"> = </span>
                  <span className="font-semibold">{cfTotal}s</span>
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  launch + load + challenge + solve — the maximum a single protected request can
                  take before giving up.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Storage Paths</CardTitle>
          <CardDescription>Directories used for media and images.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            {STORAGE_PATHS.map(([k, label]) => (
              <div key={k} className="space-y-2">
                <Label>
                  {label}
                  <OverrideMarker path={["misc", k]} />
                </Label>
                <Input
                  value={String(m[k] ?? "")}
                  onChange={(e) => setMiscPath([k], e.target.value)}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Libraries</CardTitle>
          <CardDescription>Named library roots for shows and movies.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {(["show", "movie"] as const).map((kind) => {
              const libKey = `${kind}_libraries`;
              const dirKey = `${kind}_directory`;
              const libs = (m[libKey] as Keyed<{ name: string; path: string }>[] | undefined) ?? [];
              return (
                <div key={kind} className="space-y-3">
                  <Label>
                    {kind === "show" ? "Show Libraries" : "Movie Libraries"}
                    <OverrideMarker path={["misc", libKey]} />
                  </Label>
                  <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3 lg:flex-row lg:items-center lg:gap-2 lg:rounded-none lg:border-0 lg:bg-transparent lg:p-0">
                    <div className="hidden w-5 shrink-0 lg:block" />
                    <div className="flex flex-col gap-1 lg:max-w-[200px] lg:flex-none">
                      <span className="text-xs text-muted-foreground lg:hidden">Name</span>
                      <Input value="Default" disabled />
                    </div>
                    <div className="flex flex-col gap-1 lg:flex-1">
                      <span className="text-xs text-muted-foreground lg:hidden">Path</span>
                      <Input value={String(m[dirKey] ?? "")} disabled />
                    </div>
                    <div className="hidden h-8 w-8 shrink-0 lg:block" />
                  </div>
                  {libs.map((library, i) => {
                    const moveLib = (from: number, to: number) => {
                      if (to < 0 || to >= libs.length) return;
                      const next = [...libs];
                      const [moved] = next.splice(from, 1);
                      next.splice(to, 0, moved!);
                      setMiscPath([libKey], next);
                    };
                    return (
                      <div
                        key={library._key}
                        className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3 lg:flex-row lg:items-center lg:gap-2 lg:rounded-none lg:border-0 lg:bg-transparent lg:p-0"
                      >
                        <div className="flex items-center gap-1 lg:contents">
                          <div className="flex items-center gap-1 lg:flex-col lg:gap-0.5">
                            <Button
                              variant="ghost"
                              size="icon"
                              disabled={i === 0}
                              onClick={() => moveLib(i, i - 1)}
                              title="Move up"
                              className="h-8 w-8 lg:h-5 lg:w-5"
                            >
                              ▲
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              disabled={i === libs.length - 1}
                              onClick={() => moveLib(i, i + 1)}
                              title="Move down"
                              className="h-8 w-8 lg:h-5 lg:w-5"
                            >
                              ▼
                            </Button>
                          </div>
                          <span className="text-sm font-medium lg:hidden">Library {i + 1}</span>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() =>
                              setMiscPath(
                                [libKey],
                                libs.filter((_, j) => j !== i),
                              )
                            }
                            title="Delete"
                            className="ml-auto lg:order-last lg:ml-0"
                          >
                            <Trash2 className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </div>
                        <div className="flex flex-col gap-1 lg:max-w-[200px] lg:flex-none">
                          <span className="text-xs text-muted-foreground lg:hidden">Name</span>
                          <Input
                            value={library.name}
                            onChange={(e) => {
                              const next = [...libs];
                              next[i] = { ...next[i]!, name: e.target.value };
                              setMiscPath([libKey], next);
                            }}
                            placeholder="Name"
                          />
                        </div>
                        <div className="flex flex-col gap-1 lg:flex-1">
                          <span className="text-xs text-muted-foreground lg:hidden">Path</span>
                          <Input
                            value={library.path}
                            onChange={(e) => {
                              const next = [...libs];
                              next[i] = { ...next[i]!, path: e.target.value };
                              setMiscPath([libKey], next);
                            }}
                            placeholder="Path"
                          />
                        </div>
                      </div>
                    );
                  })}
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1"
                    onClick={() =>
                      setMiscPath([libKey], [...libs, { _key: newRowKey(), name: "", path: "" }])
                    }
                  >
                    <Plus className="h-4 w-4" />
                    Add {kind === "show" ? "Show" : "Movie"} Library
                  </Button>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Naming</CardTitle>
          <CardDescription>
            Templates applied to newly imported files and folders. Invalid path characters are
            removed automatically.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            {FIRST_NAMING.map(([k, label, placeholder]) => (
              <div key={k} className="space-y-2">
                <Label>
                  {label}
                  <OverrideMarker path={["misc", "naming", k]} />
                </Label>
                <Input
                  value={String(naming[k] ?? "")}
                  onChange={(e) => setMiscPath(["naming", k], e.target.value)}
                  placeholder={placeholder}
                />
              </div>
            ))}
            <div className="space-y-2 md:col-span-2">
              <Label>
                Episode filename format
                <OverrideMarker path={["misc", "naming", "episode_file_format"]} />
              </Label>
              <Input
                value={String(naming.episode_file_format ?? "")}
                onChange={(e) => setMiscPath(["naming", "episode_file_format"], e.target.value)}
                placeholder="{show_title} S{season_number:02d}E{episode_number:02d}{suffix}"
              />
            </div>
            {LAST_NAMING.map(([k, label, placeholder]) => (
              <div key={k} className="space-y-2">
                <Label>
                  {label}
                  <OverrideMarker path={["misc", "naming", k]} />
                </Label>
                <Input
                  value={String(naming[k] ?? "")}
                  onChange={(e) => setMiscPath(["naming", k], e.target.value)}
                  placeholder={placeholder}
                />
              </div>
            ))}
          </div>
          <Separator className="my-6" />
          <div className="space-y-4">
            <div>
              <h4 className="text-sm font-semibold">Available tokens</h4>
              <p className="text-xs text-muted-foreground">
                Wrap a token in <code className="font-mono">{"{...}"}</code> to insert its value.
                Numeric tokens support Python format specs (e.g.{" "}
                <code className="font-mono">{"{season_number:02d}"}</code>).
              </p>
            </div>
            {TOKEN_GROUPS.map((group) => (
              <div key={group.heading} className="space-y-2">
                <div>
                  <p className="text-sm font-medium">{group.heading}</p>
                  <p className="font-mono text-xs text-muted-foreground">{group.scope}</p>
                </div>
                <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
                  {group.tokens.map(([token, desc]) => (
                    <React.Fragment key={token}>
                      <dt className="font-mono text-foreground">{token}</dt>
                      <dd className="text-muted-foreground">{desc}</dd>
                    </React.Fragment>
                  ))}
                </dl>
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              Reserved/invalid path characters are stripped automatically. Empty renders fall back
              to the default template.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

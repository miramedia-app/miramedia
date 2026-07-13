"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Download,
  LoaderCircle,
  RotateCcw,
  Save,
  Search as SearchIcon,
  Upload,
  X as XIcon,
} from "lucide-react";
import { DashboardHeader } from "@/components/dashboard-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import apiClient from "@/lib/api/client";

// Mimics the settings page layout (toolbar + sidebar tab list + form area)
// so the load state doesn't shift the page when content arrives.
function SettingsPageSkeleton() {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-8 min-w-[260px] flex-1" />
        <Skeleton className="h-8 w-24" />
        <Skeleton className="h-8 w-24" />
        <Skeleton className="h-8 w-32" />
      </div>
      <div className="grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          {Array.from({ length: 11 }, (_, i) => (
            <Skeleton key={`tab-${i}`} className="h-9 w-full" />
          ))}
        </div>
        <SettingsTabSkeleton />
      </div>
    </>
  );
}

// Used both for the in-tab Suspense fallback (lazy chunk load) and as the
// right pane of the full-page skeleton. Approximates two cards of form
// fields without committing to a specific tab's exact shape.
function SettingsTabSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {[0, 1].map((card) => (
        <div key={card} className="rounded-lg border bg-card p-6">
          <Skeleton className="mb-2 h-5 w-40" />
          <Skeleton className="mb-6 h-4 w-72" />
          <div className="grid gap-4 md:grid-cols-2">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={`f-${card}-${i}`} className="flex flex-col gap-1.5">
                <Skeleton className="h-3.5 w-24" />
                <Skeleton className="h-9 w-full" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

type AnyObj = Record<string, unknown>;
type Settings = AnyObj & {
  overrides?: AnyObj;
  defaults?: AnyObj;
  misc?: AnyObj;
  auth?: AnyObj;
  notifications?: AnyObj;
  torrents?: AnyObj;
  indexers?: AnyObj;
  metadata?: AnyObj;
  requests?: AnyObj;
  subtitles?: AnyObj;
  updates?: AnyObj;
  imports?: AnyObj;
};
type SchemaEntry = {
  path: string[];
  section: string;
  key: string;
  label: string;
  description: string;
  type: string;
};

const TAB_DEFS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "general", label: "General" },
  { value: "updates", label: "Updates" },
  { value: "metadata", label: "Metadata" },
  { value: "torrents", label: "Torrents" },
  { value: "indexers", label: "Indexers" },
  { value: "scores", label: "Scores" },
  { value: "subtitles", label: "Subtitles" },
  { value: "imports", label: "Imports" },
  { value: "requests", label: "Requests" },
  { value: "notifications", label: "Notifications" },
  { value: "auth", label: "Authentication" },
];

const INDEXER_SCORING_KEYS = new Set<string>([
  "quality_options",
  "codec_options",
  "title_scoring_rules",
  "indexer_flag_scoring_rules",
  "scoring_rule_sets",
  "minimum_seeders",
  "maximum_seeders",
  "min_size_mb",
  "max_size_mb",
  "preferred_languages",
  "rejected_languages",
  "recency_bonus",
  "recency_decay_days",
]);

function splitIndexer(obj: AnyObj | undefined, want: "providers" | "scoring"): AnyObj {
  const out: AnyObj = {};
  if (!obj) return out;
  for (const [k, v] of Object.entries(obj)) {
    const isScoring = INDEXER_SCORING_KEYS.has(k);
    if ((want === "scoring" && isScoring) || (want === "providers" && !isScoring)) {
      out[k] = v;
    }
  }
  return out;
}

function stableStringify(value: unknown): string {
  if (value === null || value === undefined) return JSON.stringify(value ?? null);
  if (typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(stableStringify).join(",") + "]";
  const obj = value as AnyObj;
  // `_key` is a client-only synthetic id (stable React keys); it never leaves
  // the browser, so ignore it here or dirty detection would trip permanently.
  const keys = Object.keys(obj)
    .filter((k) => k !== "_key")
    .sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + stableStringify(obj[k])).join(",") + "}";
}

// Attach a stable synthetic `_key` to each object row of a list. Non-arrays
// pass through unchanged; existing `_key`s are preserved across reloads.
function keyRows(list: unknown): unknown {
  if (!Array.isArray(list)) return list;
  return list.map((row) =>
    row && typeof row === "object" && !Array.isArray(row)
      ? { ...(row as AnyObj), _key: (row as AnyObj)._key ?? crypto.randomUUID() }
      : row,
  );
}

// Reorderable/deletable list keys that carry a synthetic `_key` in local
// state. Kept in sync with the row renderings in scores-tab / general-tab.
const INDEXER_KEYED_LISTS = [
  "quality_options",
  "codec_options",
  "title_scoring_rules",
  "indexer_flag_scoring_rules",
];
const MISC_KEYED_LISTS = ["show_libraries", "movie_libraries"];

// Deep-remove every `_key` before submitting to the API.
function stripRowKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripRowKeys);
  if (value && typeof value === "object") {
    const out: AnyObj = {};
    for (const [k, v] of Object.entries(value as AnyObj)) {
      if (k === "_key") continue;
      out[k] = stripRowKeys(v);
    }
    return out;
  }
  return value;
}

import { OverrideCtx, type OverrideCtxValue } from "./_shared";

// Tabs are code-split via React.lazy so opening Settings only loads the
// initially-active tab. Suspense boundary in the render handles fallback.
const AuthTab = React.lazy(() => import("./tabs/auth-tab").then((m) => ({ default: m.AuthTab })));
const RequestsTab = React.lazy(() =>
  import("./tabs/requests-tab").then((m) => ({ default: m.RequestsTab })),
);
const IndexersTab = React.lazy(() =>
  import("./tabs/indexers-tab").then((m) => ({ default: m.IndexersTab })),
);
const ImportsTab = React.lazy(() =>
  import("./tabs/imports-tab").then((m) => ({ default: m.ImportsTab })),
);
const MetadataTab = React.lazy(() =>
  import("./tabs/metadata-tab").then((m) => ({ default: m.MetadataTab })),
);
const UpdatesTab = React.lazy(() =>
  import("./tabs/updates-tab").then((m) => ({ default: m.UpdatesTab })),
);
const SubtitlesTab = React.lazy(() =>
  import("./tabs/subtitles-tab").then((m) => ({ default: m.SubtitlesTab })),
);
const NotificationsTab = React.lazy(() =>
  import("./tabs/notifications-tab").then((m) => ({ default: m.NotificationsTab })),
);
const TorrentsTab = React.lazy(() =>
  import("./tabs/torrents-tab").then((m) => ({ default: m.TorrentsTab })),
);
const ScoresTab = React.lazy(() =>
  import("./tabs/scores-tab").then((m) => ({ default: m.ScoresTab })),
);
const GeneralTab = React.lazy(() =>
  import("./tabs/general-tab").then((m) => ({ default: m.GeneralTab })),
);

export default function SystemSettingsPage() {
  const qc = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["system", "settings"],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/system/settings");
      return (data ?? {}) as Settings;
    },
  });
  const schemaQuery = useQuery({
    queryKey: ["system", "settings", "schema"],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/system/settings/schema");
      return (data ?? []) as unknown as SchemaEntry[];
    },
  });
  const settings = React.useMemo(
    () => settingsQuery.data ?? ({} as Settings),
    [settingsQuery.data],
  );
  const overrides = (settings.overrides ?? {}) as AnyObj;
  const defaults = (settings.defaults ?? {}) as AnyObj;
  const schema = React.useMemo(() => schemaQuery.data ?? [], [schemaQuery.data]);
  const loaded = !!settings.misc;

  // Local form state
  const [misc, setMisc] = React.useState<AnyObj>({ naming: {} });
  const [auth, setAuth] = React.useState<AnyObj>({ openid_connect: {} });
  const [notifications, setNotifications] = React.useState<AnyObj>({
    smtp_config: {},
    email_notifications: {},
    gotify: {},
    ntfy: {},
    pushover: {},
  });
  const [torrents, setTorrents] = React.useState<AnyObj>({
    qbittorrent: {},
    transmission: {},
    sabnzbd: {},
    native: {},
  });
  const [indexers, setIndexers] = React.useState<AnyObj>({
    prowlarr: {},
    jackett: {},
    native: {},
    quality_options: [],
    codec_options: [],
    title_scoring_rules: [],
    indexer_flag_scoring_rules: [],
    scoring_rule_sets: [],
  });
  const [cloudflare, setCloudflare] = React.useState<AnyObj>({});
  const [metadata, setMetadata] = React.useState<AnyObj>({
    native: { tvmaze: {}, cinemeta: {} },
    tmdb: {},
    tvdb: {},
  });
  const [requests, setRequests] = React.useState<AnyObj>({ seerr: {} });
  const [subtitles, setSubtitles] = React.useState<AnyObj>({
    native: {
      gestdown: {},
      tvsubtitles: {},
      yifysubtitles: {},
      subsource: {},
      opensubtitles: {},
      bsplayer: {},
      opensubtitlescom: {},
      addic7ed: {},
      subdl: {},
      napiprojekt: {},
      subtis: {},
      subtitulamos: {},
    },
    bazarr: {},
  });
  const [imports, setImports] = React.useState<AnyObj>({});
  const [updates, setUpdates] = React.useState<AnyObj>({});

  // Sync local state when query reloads — one effect per section so an edit
  // in one tab doesn't re-clone the other ten. React Query's structural
  // sharing keeps slice identity stable across refetches when values match.
  React.useEffect(() => {
    if (settings.misc) {
      const next = structuredClone(settings.misc) as AnyObj;
      if (!next.naming) next.naming = {};
      for (const k of MISC_KEYED_LISTS) {
        if (Array.isArray(next[k])) next[k] = keyRows(next[k]);
      }
      setMisc(next);
    }
  }, [settings.misc]);
  React.useEffect(() => {
    if (settings.auth) setAuth(structuredClone(settings.auth) as AnyObj);
  }, [settings.auth]);
  React.useEffect(() => {
    if (settings.notifications) setNotifications(structuredClone(settings.notifications) as AnyObj);
  }, [settings.notifications]);
  React.useEffect(() => {
    if (settings.torrents) setTorrents(structuredClone(settings.torrents) as AnyObj);
  }, [settings.torrents]);
  React.useEffect(() => {
    if (settings.indexers) {
      const next = structuredClone(settings.indexers) as AnyObj;
      for (const k of INDEXER_KEYED_LISTS) {
        if (Array.isArray(next[k])) next[k] = keyRows(next[k]);
      }
      setIndexers(next);
    }
  }, [settings.indexers]);
  React.useEffect(() => {
    if (settings.metadata) setMetadata(structuredClone(settings.metadata) as AnyObj);
  }, [settings.metadata]);
  React.useEffect(() => {
    if (settings.requests) setRequests(structuredClone(settings.requests) as AnyObj);
  }, [settings.requests]);
  React.useEffect(() => {
    if (settings.subtitles) setSubtitles(structuredClone(settings.subtitles) as AnyObj);
  }, [settings.subtitles]);
  React.useEffect(() => {
    if (settings.imports) setImports(structuredClone(settings.imports) as AnyObj);
  }, [settings.imports]);
  React.useEffect(() => {
    if (settings.updates) setUpdates(structuredClone(settings.updates) as AnyObj);
  }, [settings.updates]);
  React.useEffect(() => {
    if (settings.cloudflare) setCloudflare(structuredClone(settings.cloudflare) as AnyObj);
  }, [settings.cloudflare]);

  // Per-section dirty flags. Each useMemo runs `stableStringify` only over
  // its own slice, so a keystroke in one tab doesn't re-stringify the other
  // ten. Cloudflare is part of the General tab.
  const isSectionDirty = (cur: unknown, orig: unknown) =>
    orig !== undefined && stableStringify(cur) !== stableStringify(orig);

  const generalDirty = React.useMemo(
    () =>
      loaded &&
      isSectionDirty(
        { misc, cloudflare },
        { misc: settings.misc, cloudflare: settings.cloudflare },
      ),
    [loaded, misc, cloudflare, settings.misc, settings.cloudflare],
  );
  const torrentsDirty = React.useMemo(
    () => loaded && isSectionDirty(torrents, settings.torrents),
    [loaded, torrents, settings.torrents],
  );
  const indexersDirty = React.useMemo(
    () =>
      loaded &&
      isSectionDirty(
        splitIndexer(indexers, "providers"),
        splitIndexer(settings.indexers, "providers"),
      ),
    [loaded, indexers, settings.indexers],
  );
  const scoresDirty = React.useMemo(
    () =>
      loaded &&
      isSectionDirty(splitIndexer(indexers, "scoring"), splitIndexer(settings.indexers, "scoring")),
    [loaded, indexers, settings.indexers],
  );
  const notificationsDirty = React.useMemo(
    () => loaded && isSectionDirty(notifications, settings.notifications),
    [loaded, notifications, settings.notifications],
  );
  const metadataDirty = React.useMemo(
    () => loaded && isSectionDirty(metadata, settings.metadata),
    [loaded, metadata, settings.metadata],
  );
  const requestsDirty = React.useMemo(
    () => loaded && isSectionDirty(requests, settings.requests),
    [loaded, requests, settings.requests],
  );
  const subtitlesDirty = React.useMemo(
    () => loaded && isSectionDirty(subtitles, settings.subtitles),
    [loaded, subtitles, settings.subtitles],
  );
  const importsDirty = React.useMemo(
    () => loaded && isSectionDirty(imports, settings.imports),
    [loaded, imports, settings.imports],
  );
  const updatesDirty = React.useMemo(
    () => loaded && isSectionDirty(updates, settings.updates),
    [loaded, updates, settings.updates],
  );
  const authDirty = React.useMemo(
    () => loaded && isSectionDirty(auth, settings.auth),
    [loaded, auth, settings.auth],
  );

  const dirtyTabs = React.useMemo(() => {
    const dirty = new Set<string>();
    if (generalDirty) dirty.add("general");
    if (torrentsDirty) dirty.add("torrents");
    if (indexersDirty) dirty.add("indexers");
    if (scoresDirty) dirty.add("scores");
    if (notificationsDirty) dirty.add("notifications");
    if (metadataDirty) dirty.add("metadata");
    if (requestsDirty) dirty.add("requests");
    if (subtitlesDirty) dirty.add("subtitles");
    if (importsDirty) dirty.add("imports");
    if (updatesDirty) dirty.add("updates");
    if (authDirty) dirty.add("auth");
    return dirty;
  }, [
    generalDirty,
    torrentsDirty,
    indexersDirty,
    scoresDirty,
    notificationsDirty,
    metadataDirty,
    requestsDirty,
    subtitlesDirty,
    importsDirty,
    updatesDirty,
    authDirty,
  ]);

  const isDirty = dirtyTabs.size > 0;

  const [saving, setSaving] = React.useState(false);
  const [resetting, setResetting] = React.useState(false);
  // URL is source of truth for the active tab — no useState/effect mirror.
  // Clicks call setActiveTab which writes to the URL; readers derive from it.
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tabParam = searchParams?.get("tab") ?? null;
  const activeTab = tabParam && TAB_DEFS.some((t) => t.value === tabParam) ? tabParam : "general";
  const setActiveTab = React.useCallback(
    (tab: string) => {
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      if (tab === "general") params.delete("tab");
      else params.set("tab", tab);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );
  const [searchQuery, setSearchQuery] = React.useState("");

  // Unsaved warning
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: BeforeUnloadEvent) => {
      if (!isDirty || saving) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty, saving]);

  function isOverridden(section: string, ...path: string[]): boolean {
    let obj: unknown = (overrides as AnyObj)[section];
    if (!obj) return false;
    for (const key of path) {
      if (obj == null || typeof obj !== "object") return false;
      obj = (obj as AnyObj)[key];
    }
    return obj !== undefined;
  }

  async function resetField(path: string[]) {
    try {
      const { error } = await apiClient.POST("/api/v1/system/settings/override/clear", {
        body: { path },
      });
      if (error) {
        toast.error("Failed to reset field");
        return;
      }
      toast.success("Field reset to default");
      await qc.invalidateQueries({ queryKey: ["system", "settings"] });
    } catch {
      toast.error("Failed to reset field");
    }
  }

  async function saveAllSettings() {
    setSaving(true);
    try {
      const { error } = await apiClient.PUT("/api/v1/system/settings", {
        body: {
          misc: stripRowKeys(misc),
          auth,
          notifications,
          torrents,
          indexers: stripRowKeys(indexers),
          metadata,
          requests,
          subtitles,
          imports,
          updates,
          cloudflare,
        } as never,
      });
      if (error) {
        toast.error("Failed to save settings");
        return;
      }
      toast.success("Settings saved.");
      await qc.invalidateQueries({ queryKey: ["system", "settings"] });
    } catch {
      toast.error("Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  async function resetAll() {
    setResetting(true);
    try {
      await apiClient.DELETE("/api/v1/system/settings");
      toast.success("All settings reset to defaults");
      await qc.invalidateQueries({ queryKey: ["system", "settings"] });
    } catch {
      toast.error("Failed to reset settings");
    } finally {
      setResetting(false);
    }
  }

  // Export / Import
  const [exporting, setExporting] = React.useState(false);
  const [importing, setImporting] = React.useState(false);

  async function exportSettings() {
    setExporting(true);
    try {
      const { data, error } = await apiClient.GET("/api/v1/system/settings/export");
      if (error || !data) {
        toast.error("Failed to export settings");
        return;
      }
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const ts = new Date().toISOString().replace(/[:.]/g, "-");
      a.download = `miramedia-settings-${ts}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Settings exported");
    } finally {
      setExporting(false);
    }
  }

  async function importSettings(file: File) {
    const text = await file.text();
    let parsed: { overrides?: AnyObj };
    try {
      parsed = JSON.parse(text);
    } catch {
      toast.error("Invalid JSON file");
      return;
    }
    const incoming = parsed.overrides ?? null;
    if (!incoming || typeof incoming !== "object") {
      toast.error('File missing "overrides" object');
      return;
    }
    const mode: "replace" | "merge" = confirm(
      "Click OK to REPLACE all current overrides with this file, or Cancel to MERGE.",
    )
      ? "replace"
      : "merge";
    setImporting(true);
    try {
      const { error } = await apiClient.POST("/api/v1/system/settings/import", {
        body: { overrides: incoming as never, mode },
      });
      if (error) {
        toast.error("Import rejected — see server logs.");
        return;
      }
      toast.success(`Settings imported (${mode})`);
      await qc.invalidateQueries({ queryKey: ["system", "settings"] });
    } finally {
      setImporting(false);
    }
  }

  function pickImportFile() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/json,.json";
    input.onchange = () => {
      const file = input.files?.[0];
      if (file) void importSettings(file);
    };
    input.click();
  }

  // --- Sub-object setters --------------------------------------------------
  // helper that returns a setter that merges into a top-level key
  function makeNested<T extends AnyObj>(setter: React.Dispatch<React.SetStateAction<T>>) {
    return (path: string[], value: unknown) =>
      setter((prev) => {
        const next = structuredClone(prev) as AnyObj;
        let node: AnyObj = next;
        for (let i = 0; i < path.length - 1; i++) {
          const k = path[i]!;
          if (typeof node[k] !== "object" || node[k] == null) node[k] = {};
          node = node[k] as AnyObj;
        }
        node[path[path.length - 1]!] = value;
        return next as T;
      });
  }

  const setMiscPath = makeNested(setMisc);
  const setAuthPath = makeNested(setAuth);
  const setNotificationsPath = makeNested(setNotifications);
  const setTorrentsPath = makeNested(setTorrents);
  const setIndexersPath = makeNested(setIndexers);
  const setMetadataPath = makeNested(setMetadata);
  const setRequestsPath = makeNested(setRequests);
  const setSubtitlesPath = makeNested(setSubtitles);
  const setImportsPath = makeNested(setImports);
  const setUpdatesPath = makeNested(setUpdates);
  const setCloudflarePath = makeNested(setCloudflare);

  // Search results
  const searchResults = React.useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (q.length < 2) return [] as SchemaEntry[];
    return schema
      .filter(
        (entry) =>
          entry.label.toLowerCase().includes(q) ||
          entry.key.toLowerCase().includes(q) ||
          entry.description.toLowerCase().includes(q),
      )
      .slice(0, 10);
  }, [schema, searchQuery]);

  function jumpToEntry(entry: SchemaEntry) {
    const [section, ...rest] = entry.path;
    let tab = section ?? "general";
    if (section === "misc") tab = "general";
    if (section === "indexers" && INDEXER_SCORING_KEYS.has(rest[0] ?? "")) tab = "scores";
    setActiveTab(tab);
    setSearchQuery("");
    toast.info(`In ${entry.section}: ${entry.label}`, { description: entry.key });
  }

  const overrideCtx = React.useMemo<OverrideCtxValue>(
    () => ({ isOverridden, defaults, resetField }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [overrides, defaults],
  );

  return (
    <OverrideCtx.Provider value={overrideCtx}>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "System", href: "/dashboard/system/users" },
          { label: "Settings" },
        ]}
      />
      <main className="flex w-full flex-col gap-4 p-4 pt-0">
        {!loaded ? (
          <SettingsPageSkeleton />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative flex h-8 min-w-[260px] flex-1 items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-sm shadow-xs transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50">
                <SearchIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <input
                  type="search"
                  placeholder="Search settings…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="min-w-[80px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                />
                {searchQuery && (
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={() => setSearchQuery("")}
                    aria-label="Clear"
                  >
                    <XIcon className="h-3.5 w-3.5" />
                  </button>
                )}
                {searchResults.length > 0 ? (
                  <div className="absolute top-full right-0 left-0 z-10 mt-1 max-h-72 overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md">
                    {searchResults.map((entry) => (
                      <button
                        key={entry.key + entry.path.join(".")}
                        type="button"
                        className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-accent hover:text-accent-foreground"
                        onClick={() => jumpToEntry(entry)}
                      >
                        <span className="text-sm font-medium">{entry.label}</span>
                        <span className="text-xs text-muted-foreground">
                          {entry.key} · {entry.type}
                        </span>
                        {entry.description && (
                          <span className="text-xs text-muted-foreground">{entry.description}</span>
                        )}
                      </button>
                    ))}
                  </div>
                ) : searchQuery.trim().length >= 2 ? (
                  <div className="absolute top-full right-0 left-0 z-10 mt-1 rounded-md border bg-popover px-3 py-2 text-sm text-muted-foreground shadow-md">
                    No matching settings.
                  </div>
                ) : null}
              </div>
              <span className="hidden h-6 w-px bg-border sm:block" />
              <Button
                variant="outline"
                size="default"
                className="text-xs"
                onClick={() => void exportSettings()}
                disabled={exporting}
              >
                {exporting ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Download className="mr-1 h-4 w-4" />
                )}
                Export
              </Button>
              <Button
                variant="outline"
                size="default"
                className="text-xs"
                onClick={pickImportFile}
                disabled={importing}
              >
                {importing ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="mr-1 h-4 w-4" />
                )}
                Import
              </Button>
              <Button
                variant="outline"
                size="default"
                className="text-xs"
                onClick={() => void resetAll()}
                disabled={resetting}
              >
                {resetting ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <RotateCcw className="mr-1 h-4 w-4" />
                )}
                Reset All
              </Button>
              <span className="hidden h-6 w-px bg-border sm:block" />
              {isDirty && (
                <span className="text-xs text-muted-foreground">
                  {dirtyTabs.size} unsaved {dirtyTabs.size === 1 ? "section" : "sections"}
                </span>
              )}
              <Button
                onClick={() => void saveAllSettings()}
                disabled={saving || !isDirty}
                size="default"
                className="text-xs"
              >
                {saving ? (
                  <LoaderCircle className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-1 h-4 w-4" />
                )}
                Save Settings{isDirty ? ` (${dirtyTabs.size})` : ""}
              </Button>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
              <div className="grid gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
                <TabsList className="sticky top-4 flex h-auto flex-col items-stretch justify-start self-start bg-transparent p-0">
                  {TAB_DEFS.map(({ value, label }) => (
                    <TabsTrigger
                      key={value}
                      value={value}
                      className="relative justify-start rounded-md px-3 py-2 text-left text-sm font-medium data-[state=active]:bg-muted data-[state=active]:shadow-none"
                    >
                      {label}
                      {dirtyTabs.has(value) && (
                        <span
                          className="ml-auto h-2 w-2 rounded-full bg-primary"
                          title="Unsaved changes"
                          aria-label={`Unsaved changes in ${label}`}
                        />
                      )}
                    </TabsTrigger>
                  ))}
                </TabsList>
                <div className="min-w-0">
                  <React.Suspense fallback={<SettingsTabSkeleton />}>
                    {/* General */}
                    <TabsContent value="general">
                      <GeneralTab
                        misc={misc}
                        setMiscPath={setMiscPath}
                        cloudflare={cloudflare}
                        setCloudflarePath={setCloudflarePath}
                      />
                    </TabsContent>

                    {/* Torrents */}
                    <TabsContent value="torrents">
                      <TorrentsTab
                        misc={misc}
                        setMiscPath={setMiscPath}
                        torrents={torrents}
                        setTorrentsPath={setTorrentsPath}
                      />
                    </TabsContent>

                    {/* Indexers */}
                    <TabsContent value="indexers">
                      <IndexersTab
                        indexers={indexers}
                        setIndexersPath={setIndexersPath}
                        misc={misc}
                        setMiscPath={setMiscPath}
                      />
                    </TabsContent>

                    {/* Scores */}
                    <TabsContent value="scores">
                      <ScoresTab indexers={indexers} setIndexersPath={setIndexersPath} />
                    </TabsContent>

                    {/* Notifications */}
                    <TabsContent value="notifications">
                      <NotificationsTab
                        notifications={notifications}
                        setNotificationsPath={setNotificationsPath}
                      />
                    </TabsContent>

                    {/* Metadata */}
                    <TabsContent value="metadata">
                      <MetadataTab metadata={metadata} setMetadataPath={setMetadataPath} />
                    </TabsContent>

                    {/* Requests */}
                    <TabsContent value="requests">
                      <RequestsTab requests={requests} setRequestsPath={setRequestsPath} />
                    </TabsContent>

                    {/* Subtitles */}
                    <TabsContent value="imports">
                      <ImportsTab
                        imports={imports}
                        setImportsPath={setImportsPath}
                        misc={misc}
                        setMiscPath={setMiscPath}
                      />
                    </TabsContent>

                    <TabsContent value="subtitles">
                      <SubtitlesTab subtitles={subtitles} setSubtitlesPath={setSubtitlesPath} />
                    </TabsContent>

                    {/* Updates */}
                    <TabsContent value="updates" className="space-y-4">
                      <UpdatesTab updates={updates} setUpdatesPath={setUpdatesPath} />
                    </TabsContent>

                    {/* Auth */}
                    <TabsContent value="auth" className="space-y-4">
                      <AuthTab auth={auth} setAuthPath={setAuthPath} />
                    </TabsContent>
                  </React.Suspense>
                  <p className="mt-4 text-xs text-muted-foreground">
                    Saved values override{" "}
                    <code className="rounded bg-muted px-1 py-0.5 text-[0.7rem]">config.toml</code>.
                    Fields with an{" "}
                    <Badge variant="outline" className="mx-0.5 text-[0.7rem]">
                      overridden
                    </Badge>{" "}
                    badge differ from the file default — hover for the original or click the reset
                    icon to revert. Changes apply immediately, no restart required.
                  </p>
                </div>
              </div>
            </Tabs>
          </>
        )}
      </main>
    </OverrideCtx.Provider>
  );
}

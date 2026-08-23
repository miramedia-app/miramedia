"use client";

import * as React from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import apiClient from "@/lib/api/client";
import type { AnyObj, SetPath } from "./_shared";
import { newRowKey } from "./_shared";

export const INDEXER_SCORING_KEYS = new Set<string>([
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

/** Reorderable/deletable list keys that carry a synthetic `_key` in local state. */
export const INDEXER_KEYED_LISTS = [
  "quality_options",
  "codec_options",
  "title_scoring_rules",
  "indexer_flag_scoring_rules",
] as const;

export const MISC_KEYED_LISTS = ["show_libraries", "movie_libraries"] as const;

export type SettingsEditorSections = {
  misc: AnyObj;
  auth: AnyObj;
  notifications: AnyObj;
  torrents: AnyObj;
  indexers: AnyObj;
  metadata: AnyObj;
  requests: AnyObj;
  subtitles: AnyObj;
  imports: AnyObj;
  updates: AnyObj;
  cloudflare: AnyObj;
  watchlists: AnyObj;
  streams: AnyObj;
  playback: AnyObj;
};

export type SettingsSnapshot = Partial<SettingsEditorSections> & {
  overrides?: AnyObj;
  defaults?: AnyObj;
};

export function splitIndexer(obj: AnyObj | undefined, want: "providers" | "scoring"): AnyObj {
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

export function stableStringify(value: unknown): string {
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

/** Attach a stable synthetic `_key` to each object row of a list. */
export function keyRows(list: unknown): unknown {
  if (!Array.isArray(list)) return list;
  return list.map((row) =>
    row && typeof row === "object" && !Array.isArray(row)
      ? { ...(row as AnyObj), _key: (row as AnyObj)._key ?? newRowKey() }
      : row,
  );
}

/** Deep-remove every `_key` before submitting to the API. */
export function stripRowKeys(value: unknown): unknown {
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

export function initMiscFromServer(misc: AnyObj): AnyObj {
  const next = structuredClone(misc) as AnyObj;
  if (!next.naming) next.naming = {};
  for (const k of MISC_KEYED_LISTS) {
    if (Array.isArray(next[k])) next[k] = keyRows(next[k]);
  }
  return next;
}

export function initIndexersFromServer(indexers: AnyObj): AnyObj {
  const next = structuredClone(indexers) as AnyObj;
  for (const k of INDEXER_KEYED_LISTS) {
    if (Array.isArray(next[k])) next[k] = keyRows(next[k]);
  }
  return next;
}

export function isSectionDirty(cur: unknown, orig: unknown): boolean {
  return orig !== undefined && stableStringify(cur) !== stableStringify(orig);
}

export function computeDirtyTabs(
  loaded: boolean,
  local: SettingsEditorSections,
  server: Partial<SettingsEditorSections>,
): Set<string> {
  const dirty = new Set<string>();
  if (!loaded) return dirty;

  if (
    isSectionDirty(
      { misc: local.misc, cloudflare: local.cloudflare },
      { misc: server.misc, cloudflare: server.cloudflare },
    )
  ) {
    dirty.add("general");
  }
  if (isSectionDirty(local.torrents, server.torrents)) dirty.add("torrents");
  if (
    isSectionDirty(
      splitIndexer(local.indexers, "providers"),
      splitIndexer(server.indexers, "providers"),
    )
  ) {
    dirty.add("indexers");
  }
  if (
    isSectionDirty(
      splitIndexer(local.indexers, "scoring"),
      splitIndexer(server.indexers, "scoring"),
    )
  ) {
    dirty.add("scores");
  }
  if (isSectionDirty(local.notifications, server.notifications)) dirty.add("notifications");
  if (isSectionDirty(local.metadata, server.metadata)) dirty.add("metadata");
  if (isSectionDirty(local.requests, server.requests)) dirty.add("requests");
  if (isSectionDirty(local.watchlists, server.watchlists)) dirty.add("watchlists");
  if (
    isSectionDirty(local.streams, server.streams) ||
    isSectionDirty(local.playback, server.playback)
  ) {
    dirty.add("playback");
  }
  if (isSectionDirty(local.subtitles, server.subtitles)) dirty.add("subtitles");
  if (isSectionDirty(local.imports, server.imports)) dirty.add("imports");
  if (isSectionDirty(local.updates, server.updates)) dirty.add("updates");
  if (isSectionDirty(local.auth, server.auth)) dirty.add("auth");
  return dirty;
}

/**
 * Build the PUT body from dirty tabs only.
 *
 * The API merges partial patches, so unchanged tabs stay out of the payload.
 */
export function composeSavePayload(
  local: SettingsEditorSections,
  dirtyTabs: ReadonlySet<string>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (dirtyTabs.has("general")) {
    payload.misc = stripRowKeys(local.misc);
    payload.cloudflare = local.cloudflare;
  }
  if (dirtyTabs.has("auth")) payload.auth = local.auth;
  if (dirtyTabs.has("notifications")) payload.notifications = local.notifications;
  if (dirtyTabs.has("torrents")) payload.torrents = local.torrents;
  if (dirtyTabs.has("indexers") || dirtyTabs.has("scores")) {
    payload.indexers = stripRowKeys(local.indexers);
  }
  if (dirtyTabs.has("metadata")) payload.metadata = local.metadata;
  if (dirtyTabs.has("requests")) payload.requests = local.requests;
  if (dirtyTabs.has("watchlists")) payload.watchlists = local.watchlists;
  if (dirtyTabs.has("playback")) {
    payload.streams = local.streams;
    payload.playback = local.playback;
  }
  if (dirtyTabs.has("subtitles")) payload.subtitles = local.subtitles;
  if (dirtyTabs.has("imports")) payload.imports = local.imports;
  if (dirtyTabs.has("updates")) payload.updates = local.updates;
  return payload;
}

export type ParseImportResult = { ok: true; overrides: AnyObj } | { ok: false; error: string };

export function parseImportOverrides(text: string): ParseImportResult {
  let parsed: { overrides?: unknown };
  try {
    parsed = JSON.parse(text) as { overrides?: unknown };
  } catch {
    return { ok: false, error: "Invalid JSON file" };
  }
  const incoming = parsed.overrides ?? null;
  if (!incoming || typeof incoming !== "object") {
    return { ok: false, error: 'File missing "overrides" object' };
  }
  return { ok: true, overrides: incoming as AnyObj };
}

/** Bodyless DELETE of all settings overrides. */
export async function resetAllSettings(
  qc: QueryClient,
  setResetting: (value: boolean) => void,
): Promise<void> {
  setResetting(true);
  try {
    const { error } = await apiClient.DELETE("/api/v1/system/settings");
    if (error) {
      toast.error("Failed to reset settings");
      return;
    }
    toast.success("All settings reset to defaults");
    await qc.invalidateQueries({ queryKey: ["system", "settings"] });
    await qc.invalidateQueries({ queryKey: ["features"] });
  } catch {
    toast.error("Failed to reset settings");
  } finally {
    setResetting(false);
  }
}

function makeNested<T extends AnyObj>(setter: React.Dispatch<React.SetStateAction<T>>): SetPath {
  return (path, value) =>
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

export function useSettingsEditor(args: { settings: SettingsSnapshot; loaded: boolean }) {
  const { settings, loaded } = args;
  const qc = useQueryClient();

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
  const [watchlists, setWatchlists] = React.useState<AnyObj>({ native: {} });
  const [streams, setStreams] = React.useState<AnyObj>({});
  const [playback, setPlayback] = React.useState<AnyObj>({});
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
    if (settings.misc) setMisc(initMiscFromServer(settings.misc));
  }, [settings.misc]);
  React.useEffect(() => {
    if (settings.auth) setAuth(structuredClone(settings.auth));
  }, [settings.auth]);
  React.useEffect(() => {
    if (settings.notifications) setNotifications(structuredClone(settings.notifications));
  }, [settings.notifications]);
  React.useEffect(() => {
    if (settings.torrents) setTorrents(structuredClone(settings.torrents));
  }, [settings.torrents]);
  React.useEffect(() => {
    if (settings.indexers) setIndexers(initIndexersFromServer(settings.indexers));
  }, [settings.indexers]);
  React.useEffect(() => {
    if (settings.metadata) setMetadata(structuredClone(settings.metadata));
  }, [settings.metadata]);
  React.useEffect(() => {
    if (settings.requests) setRequests(structuredClone(settings.requests));
  }, [settings.requests]);
  React.useEffect(() => {
    if (settings.watchlists) setWatchlists(structuredClone(settings.watchlists));
  }, [settings.watchlists]);
  React.useEffect(() => {
    if (settings.streams) setStreams(structuredClone(settings.streams));
  }, [settings.streams]);
  React.useEffect(() => {
    if (settings.playback) setPlayback(structuredClone(settings.playback));
  }, [settings.playback]);
  React.useEffect(() => {
    if (settings.subtitles) setSubtitles(structuredClone(settings.subtitles));
  }, [settings.subtitles]);
  React.useEffect(() => {
    if (settings.imports) setImports(structuredClone(settings.imports));
  }, [settings.imports]);
  React.useEffect(() => {
    if (settings.updates) setUpdates(structuredClone(settings.updates));
  }, [settings.updates]);
  React.useEffect(() => {
    if (settings.cloudflare) setCloudflare(structuredClone(settings.cloudflare));
  }, [settings.cloudflare]);

  const dirtyTabs = React.useMemo(
    () =>
      computeDirtyTabs(
        loaded,
        {
          misc,
          auth,
          notifications,
          torrents,
          indexers,
          metadata,
          requests,
          watchlists,
          streams,
          playback,
          subtitles,
          imports,
          updates,
          cloudflare,
        },
        {
          misc: settings.misc,
          auth: settings.auth,
          notifications: settings.notifications,
          torrents: settings.torrents,
          indexers: settings.indexers,
          metadata: settings.metadata,
          requests: settings.requests,
          watchlists: settings.watchlists,
          streams: settings.streams,
          playback: settings.playback,
          subtitles: settings.subtitles,
          imports: settings.imports,
          updates: settings.updates,
          cloudflare: settings.cloudflare,
        },
      ),
    [
      loaded,
      misc,
      auth,
      notifications,
      torrents,
      indexers,
      metadata,
      requests,
      watchlists,
      streams,
      playback,
      subtitles,
      imports,
      updates,
      cloudflare,
      settings.misc,
      settings.auth,
      settings.notifications,
      settings.torrents,
      settings.indexers,
      settings.metadata,
      settings.requests,
      settings.watchlists,
      settings.streams,
      settings.playback,
      settings.subtitles,
      settings.imports,
      settings.updates,
      settings.cloudflare,
    ],
  );

  const isDirty = dirtyTabs.size > 0;

  const [saving, setSaving] = React.useState(false);
  const [resetting, setResetting] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [importing, setImporting] = React.useState(false);

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

  const overrides = (settings.overrides ?? {}) as AnyObj;
  const defaults = (settings.defaults ?? {}) as AnyObj;

  function isOverridden(section: string, ...path: string[]): boolean {
    let obj: unknown = overrides[section];
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
      await qc.invalidateQueries({ queryKey: ["features"] });
    } catch {
      toast.error("Failed to reset field");
    }
  }

  async function saveAllSettings() {
    setSaving(true);
    try {
      const { error } = await apiClient.PUT("/api/v1/system/settings", {
        body: composeSavePayload(
          {
            misc,
            auth,
            notifications,
            torrents,
            indexers,
            metadata,
            requests,
            watchlists,
            streams,
            playback,
            subtitles,
            imports,
            updates,
            cloudflare,
          },
          dirtyTabs,
        ) as never,
      });
      if (error) {
        toast.error("Failed to save settings");
        return;
      }
      toast.success("Settings saved.");
      await qc.invalidateQueries({ queryKey: ["system", "settings"] });
      await qc.invalidateQueries({ queryKey: ["features"] });
    } catch {
      toast.error("Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  async function resetAll() {
    await resetAllSettings(qc, setResetting);
  }

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
    const parsed = parseImportOverrides(text);
    if (!parsed.ok) {
      toast.error(parsed.error);
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
        body: { overrides: parsed.overrides as never, mode },
      });
      if (error) {
        toast.error("Import rejected — see server logs.");
        return;
      }
      toast.success(`Settings imported (${mode})`);
      await qc.invalidateQueries({ queryKey: ["system", "settings"] });
      await qc.invalidateQueries({ queryKey: ["features"] });
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

  return {
    misc,
    setMiscPath: makeNested(setMisc),
    auth,
    setAuthPath: makeNested(setAuth),
    notifications,
    setNotificationsPath: makeNested(setNotifications),
    torrents,
    setTorrentsPath: makeNested(setTorrents),
    indexers,
    setIndexersPath: makeNested(setIndexers),
    metadata,
    setMetadataPath: makeNested(setMetadata),
    requests,
    setRequestsPath: makeNested(setRequests),
    watchlists,
    setWatchlistsPath: makeNested(setWatchlists),
    streams,
    setStreamsPath: makeNested(setStreams),
    playback,
    setPlaybackPath: makeNested(setPlayback),
    subtitles,
    setSubtitlesPath: makeNested(setSubtitles),
    imports,
    setImportsPath: makeNested(setImports),
    updates,
    setUpdatesPath: makeNested(setUpdates),
    cloudflare,
    setCloudflarePath: makeNested(setCloudflare),
    dirtyTabs,
    isDirty,
    saving,
    resetting,
    exporting,
    importing,
    saveAllSettings,
    resetAll,
    exportSettings,
    pickImportFile,
    isOverridden,
    resetField,
    defaults,
    overrides,
  };
}

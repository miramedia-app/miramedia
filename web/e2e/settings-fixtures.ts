import type { Request } from "@playwright/test";
import type { ApiHandler } from "./fixtures";

// Minimally valid SystemSettingsRead / SettingsSchemaEntry payloads for the
// settings control-plane suite. Shapes match committed OpenAPI
// (`SystemSettingsRead`, `SettingsSchemaEntry`) and config.example.toml —
// never an empty `{}`. Secrets use the server mask sentinel or redacted fakes.

/** Must match `SECRET_MASK` in `web/src/lib/secret-mask.ts`. */
export const SECRET_MASK = "********";

export const FRONTEND_URL_DEFAULT = "http://localhost:8000";
export const FRONTEND_URL_OVERRIDE = "https://settings.example.com";
export const TMDB_API_KEY_FIXTURE = "fixture-tmdb-key-not-a-secret";

/** Flat schema index entries (OpenAPI `SettingsSchemaEntry`). */
export const SETTINGS_SCHEMA_FIXTURE = [
  {
    path: ["misc", "frontend_url"],
    section: "misc",
    key: "misc.frontend_url",
    label: "Frontend Url",
    description: "Public URL the frontend is served from.",
    type: "string",
  },
  {
    path: ["metadata", "tmdb", "api_key"],
    section: "metadata",
    key: "metadata.tmdb.api_key",
    label: "Api Key",
    description: "TMDB API key.",
    type: "string",
  },
  {
    path: ["misc", "development"],
    section: "misc",
    key: "misc.development",
    label: "Development",
    description: "Enable developer mode.",
    type: "boolean",
  },
] as const;

/** TOML-shaped defaults used for override tooltips / reset. */
export const SETTINGS_DEFAULTS = {
  misc: {
    frontend_url: FRONTEND_URL_DEFAULT,
    cors_urls: ["http://localhost:8000"],
    development: false,
    continuous_download: true,
    download_specials: false,
    cleanup_after_import: true,
    naming: {
      movie_folder_format: "{title} ({year}) {provider_tag}",
      show_folder_format: "{title} ({year}) {provider_tag}",
      season_folder_format: "Season {season_number}",
      movie_file_format: "{title} ({year}){suffix}",
      episode_file_format: "{show_title} S{season_number:02d}E{episode_number:02d}{suffix}",
    },
    show_libraries: [] as unknown[],
    movie_libraries: [] as unknown[],
  },
  auth: {
    email_password_resets: false,
    openid_connect: {
      enabled: false,
      client_id: "",
      client_secret: "",
      configuration_endpoint: "",
      name: "OpenID",
    },
  },
  notifications: {
    smtp_config: {},
    email_notifications: {},
    gotify: {},
    ntfy: {},
    pushover: {},
  },
  torrents: {
    qbittorrent: {},
    transmission: {},
    sabnzbd: {},
    native: {},
  },
  indexers: {
    prowlarr: {},
    jackett: {},
    native: {},
    quality_options: [],
    codec_options: [],
    title_scoring_rules: [],
    indexer_flag_scoring_rules: [],
    scoring_rule_sets: [],
  },
  metadata: {
    desired_languages: ["en"],
    check_interval_hours: 24,
    native: {
      tvmaze: { enabled: true },
      cinemeta: { enabled: true },
    },
    tmdb: {
      enabled: false,
      api_key: null as string | null,
      default_language: "en",
      primary_languages: [] as string[],
    },
    tvdb: {
      enabled: false,
      api_key: null as string | null,
    },
  },
  requests: { seerr: {} },
  subtitles: {
    native: {},
    bazarr: {},
  },
  updates: {},
  cloudflare: { solver: "native" },
  imports: {},
};

export type SettingsOverrides = {
  misc?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  [section: string]: unknown;
};

function deepMerge(
  base: Record<string, unknown>,
  overrides: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(overrides)) {
    const prior = out[key];
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      prior &&
      typeof prior === "object" &&
      !Array.isArray(prior)
    ) {
      out[key] = deepMerge(prior as Record<string, unknown>, value as Record<string, unknown>);
    } else {
      out[key] = value;
    }
  }
  return out;
}

/**
 * Build a `SystemSettingsRead`-shaped body: effective sections + raw overrides +
 * TOML defaults (as the live GET returns).
 */
export function buildSettingsRead(overrides: SettingsOverrides = {}): Record<string, unknown> {
  const defaults = structuredClone(SETTINGS_DEFAULTS) as Record<string, unknown>;
  const effective = deepMerge(defaults, overrides as Record<string, unknown>);
  return {
    ...effective,
    overrides: structuredClone(overrides),
    defaults: structuredClone(SETTINGS_DEFAULTS),
  };
}

/** Default smoke load: one scalar override + one masked nested secret. */
export function defaultSettingsRead(): Record<string, unknown> {
  return buildSettingsRead({
    misc: { frontend_url: FRONTEND_URL_OVERRIDE },
    metadata: {
      tmdb: {
        enabled: true,
        api_key: SECRET_MASK,
        default_language: "en",
        primary_languages: [],
      },
    },
  });
}

export interface SettingsMockState {
  /** Current GET body; mutate after successful PUT/DELETE/clear. */
  read: Record<string, unknown>;
  /**
   * When true, GET /settings returns 500. Survives React Query retries / Strict
   * Mode remounts — flip false before clicking Retry in the browser.
   */
  failSettingsGet: boolean;
  /** Fail the next N PUT /settings calls. */
  settingsPutFailuresRemaining: number;
}

export function createSettingsMockState(
  initial: Record<string, unknown> = defaultSettingsRead(),
): SettingsMockState {
  return {
    read: structuredClone(initial),
    failSettingsGet: false,
    settingsPutFailuresRemaining: 0,
  };
}

/**
 * Page-specific `/api/v1/system/settings*` handlers. Merge into `installApiMock`.
 * Unexpected `/api/**` paths still go through the fixtures 501 path.
 */
export function settingsApiRoutes(state: SettingsMockState): Record<string, ApiHandler> {
  return {
    "GET /api/v1/system/settings/schema": () => ({
      body: SETTINGS_SCHEMA_FIXTURE,
    }),
    "GET /api/v1/system/settings": () => {
      if (state.failSettingsGet) {
        return { status: 500, body: { detail: "settings unavailable" } };
      }
      return { body: state.read };
    },
    "PUT /api/v1/system/settings": (req: Request) => {
      if (state.settingsPutFailuresRemaining > 0) {
        state.settingsPutFailuresRemaining -= 1;
        return { status: 500, body: { detail: "save rejected" } };
      }
      const patch = JSON.parse(req.postData() ?? "{}") as Record<string, unknown>;
      // Echo the client sections as the new effective config. Do not remask secrets
      // to the same sentinel the prior GET returned — React Query structural sharing
      // would keep `settings.metadata` referentially equal, the sync effect would
      // not run, and the secret-aware tab would stay dirty forever.
      const misc = patch.misc as Record<string, unknown> | undefined;
      const metadata = patch.metadata as Record<string, unknown> | undefined;
      const tmdb = metadata?.tmdb as Record<string, unknown> | undefined;
      const nextOverrides: SettingsOverrides = {};
      if (misc && typeof misc.frontend_url === "string") {
        nextOverrides.misc = { frontend_url: misc.frontend_url };
      }
      if (tmdb) {
        nextOverrides.metadata = {
          tmdb: {
            enabled: tmdb.enabled ?? false,
            // Store mask in overrides metadata for badge realism; effective GET
            // still echoes the client value so dirty detection can clear.
            api_key:
              typeof tmdb.api_key === "string" && tmdb.api_key.length > 0
                ? SECRET_MASK
                : tmdb.api_key,
            default_language: tmdb.default_language ?? "en",
            primary_languages: tmdb.primary_languages ?? [],
          },
        };
      }
      state.read = {
        ...(structuredClone(SETTINGS_DEFAULTS) as Record<string, unknown>),
        ...structuredClone(patch),
        overrides: nextOverrides,
        defaults: structuredClone(SETTINGS_DEFAULTS),
      };
      return { body: state.read };
    },
    "DELETE /api/v1/system/settings": () => {
      state.read = buildSettingsRead({});
      return { status: 204 };
    },
    "POST /api/v1/system/settings/override/clear": (req: Request) => {
      const body = JSON.parse(req.postData() ?? "{}") as { path?: string[] };
      const path = body.path ?? [];
      const overrides = structuredClone(state.read.overrides ?? {}) as Record<string, unknown>;
      clearPath(overrides, path);
      state.read = buildSettingsRead(overrides as SettingsOverrides);
      const metadata = state.read.metadata as Record<string, unknown> | undefined;
      const tmdb = metadata?.tmdb as Record<string, unknown> | undefined;
      const ovMeta = (overrides.metadata as Record<string, unknown> | undefined)?.tmdb as
        | Record<string, unknown>
        | undefined;
      if (tmdb && ovMeta && typeof ovMeta.api_key === "string") {
        tmdb.api_key = SECRET_MASK;
      }
      return { body: state.read };
    },
    "POST /api/v1/system/settings/import": () => ({
      status: 400,
      body: { detail: "import should not run for malformed files" },
    }),
    "GET /api/v1/system/settings/export": () => ({
      body: { overrides: state.read.overrides ?? {} },
    }),
  };
}

function clearPath(obj: Record<string, unknown>, path: string[]): void {
  if (path.length === 0) return;
  let node: Record<string, unknown> = obj;
  for (let i = 0; i < path.length - 1; i++) {
    const key = path[i]!;
    const next = node[key];
    if (!next || typeof next !== "object" || Array.isArray(next)) return;
    node = next as Record<string, unknown>;
  }
  delete node[path[path.length - 1]!];
}

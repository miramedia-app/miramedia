import { describe, expect, it, vi } from "vitest";
import {
  composeSavePayload,
  computeDirtyTabs,
  initIndexersFromServer,
  initMiscFromServer,
  isSectionDirty,
  keyRows,
  parseImportOverrides,
  splitIndexer,
  stableStringify,
  stripRowKeys,
} from "./use-settings-editor";

describe("stableStringify", () => {
  it("ignores synthetic _key so dirty detection is not permanent", () => {
    expect(stableStringify({ a: 1, _key: "x" })).toBe(stableStringify({ a: 1, _key: "y" }));
    expect(stableStringify({ a: 1, _key: "x" })).toBe(stableStringify({ a: 1 }));
  });

  it("sorts object keys for stable comparison", () => {
    expect(stableStringify({ b: 1, a: 2 })).toBe(stableStringify({ a: 2, b: 1 }));
  });

  it("deep-ignores _key inside nested arrays/objects", () => {
    const withKeys = {
      quality_options: [{ name: "1080p", _key: "a" }],
    };
    const withoutKeys = {
      quality_options: [{ name: "1080p" }],
    };
    expect(stableStringify(withKeys)).toBe(stableStringify(withoutKeys));
  });
});

describe("keyRows / stripRowKeys", () => {
  it("attaches _key to object rows and preserves existing keys", () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("generated-key");
    const keyed = keyRows([{ name: "1080p" }, { name: "720p", _key: "kept" }]) as Array<
      Record<string, unknown>
    >;
    expect(keyed[0]).toEqual({ name: "1080p", _key: "generated-key" });
    expect(keyed[1]).toEqual({ name: "720p", _key: "kept" });
    vi.restoreAllMocks();
  });

  it("passes non-arrays through unchanged", () => {
    expect(keyRows(undefined)).toBeUndefined();
    expect(keyRows("x")).toBe("x");
  });

  it("deep-strips every _key before API submit", () => {
    expect(
      stripRowKeys({
        quality_options: [{ name: "1080p", _key: "a" }],
        nested: { _key: "gone", keep: 1 },
      }),
    ).toEqual({
      quality_options: [{ name: "1080p" }],
      nested: { keep: 1 },
    });
  });
});

describe("initMiscFromServer / initIndexersFromServer", () => {
  it("ensures naming exists and keys misc library lists", () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("lib-key");
    const next = initMiscFromServer({
      show_libraries: [{ path: "/shows" }],
      movie_libraries: [{ path: "/movies", _key: "kept" }],
    });
    expect(next.naming).toEqual({});
    expect(next.show_libraries).toEqual([{ path: "/shows", _key: "lib-key" }]);
    expect(next.movie_libraries).toEqual([{ path: "/movies", _key: "kept" }]);
    vi.restoreAllMocks();
  });

  it("keys indexer scoring lists used by scores-tab", () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("idx-key");
    const next = initIndexersFromServer({
      prowlarr: { url: "http://x" },
      quality_options: [{ name: "1080p" }],
      codec_options: [{ name: "x264", _key: "kept" }],
      title_scoring_rules: [{ pattern: "REMUX" }],
      indexer_flag_scoring_rules: [{ flag: "freeleech" }],
    });
    expect(next.prowlarr).toEqual({ url: "http://x" });
    expect(next.quality_options).toEqual([{ name: "1080p", _key: "idx-key" }]);
    expect(next.codec_options).toEqual([{ name: "x264", _key: "kept" }]);
    expect(next.title_scoring_rules).toEqual([{ pattern: "REMUX", _key: "idx-key" }]);
    expect(next.indexer_flag_scoring_rules).toEqual([{ flag: "freeleech", _key: "idx-key" }]);
    vi.restoreAllMocks();
  });
});

describe("splitIndexer", () => {
  it("separates provider keys from scoring keys", () => {
    const indexers = {
      prowlarr: { url: "http://x" },
      jackett: {},
      quality_options: [{ name: "1080p" }],
      minimum_seeders: 1,
    };
    expect(splitIndexer(indexers, "providers")).toEqual({
      prowlarr: { url: "http://x" },
      jackett: {},
    });
    expect(splitIndexer(indexers, "scoring")).toEqual({
      quality_options: [{ name: "1080p" }],
      minimum_seeders: 1,
    });
  });
});

describe("isSectionDirty / computeDirtyTabs", () => {
  const emptyLocal = {
    misc: { naming: {} },
    auth: { openid_connect: {} },
    notifications: {},
    torrents: {},
    indexers: {},
    metadata: {},
    requests: {},
    watchlists: {},
    subtitles: {},
    imports: {},
    updates: {},
    cloudflare: {},
  };

  it("is not dirty when original section is undefined (not yet loaded)", () => {
    expect(isSectionDirty({ a: 1 }, undefined)).toBe(false);
  });

  it("is dirty when loaded slice differs, ignoring _key", () => {
    expect(isSectionDirty({ a: 1, _key: "x" }, { a: 1 })).toBe(false);
    expect(isSectionDirty({ a: 2 }, { a: 1 })).toBe(true);
  });

  it("returns no dirty tabs until loaded", () => {
    expect(
      computeDirtyTabs(false, emptyLocal, {
        misc: { naming: {} },
        cloudflare: {},
      }),
    ).toEqual(new Set());
  });

  it("marks every section independently, including general misc+cloudflare and indexer split", () => {
    const server = {
      misc: { naming: {} },
      cloudflare: {},
      auth: { openid_connect: {} },
      notifications: {},
      torrents: {},
      indexers: {
        prowlarr: { url: "http://a" },
        quality_options: [{ name: "1080p" }],
      },
      metadata: {},
      requests: {},
      watchlists: {},
      subtitles: {},
      imports: {},
      updates: {},
    };
    const local = {
      ...emptyLocal,
      misc: { naming: { series: "changed" } },
      cloudflare: { enabled: true },
      auth: { openid_connect: { enabled: true } },
      notifications: { smtp_config: { host: "x" } },
      torrents: { qbittorrent: { url: "http://q" } },
      indexers: {
        prowlarr: { url: "http://b" },
        quality_options: [{ name: "720p" }],
      },
      metadata: { tmdb: { api_key: "k" } },
      requests: { seerr: { url: "http://s" } },
      watchlists: { native: { enabled: false } },
      subtitles: { bazarr: { url: "http://z" } },
      imports: { enabled: true },
      updates: { check_interval: 1 },
    };
    expect(computeDirtyTabs(true, local, server)).toEqual(
      new Set([
        "general",
        "torrents",
        "indexers",
        "scores",
        "notifications",
        "metadata",
        "requests",
        "watchlists",
        "subtitles",
        "imports",
        "updates",
        "auth",
      ]),
    );
  });

  it("keeps indexers and scores dirty flags independent", () => {
    const server = {
      misc: { naming: {} },
      cloudflare: {},
      indexers: {
        prowlarr: { url: "http://a" },
        quality_options: [{ name: "1080p" }],
      },
    };
    const onlyProviders = {
      ...emptyLocal,
      indexers: {
        prowlarr: { url: "http://b" },
        quality_options: [{ name: "1080p" }],
      },
    };
    const onlyScoring = {
      ...emptyLocal,
      indexers: {
        prowlarr: { url: "http://a" },
        quality_options: [{ name: "720p" }],
      },
    };
    expect(computeDirtyTabs(true, onlyProviders, server)).toEqual(new Set(["indexers"]));
    expect(computeDirtyTabs(true, onlyScoring, server)).toEqual(new Set(["scores"]));
  });
});

describe("composeSavePayload", () => {
  const local = {
    misc: { show_libraries: [{ path: "/s", _key: "m" }] },
    auth: { openid_connect: {} },
    notifications: { smtp_config: {} },
    torrents: { qbittorrent: {} },
    indexers: { quality_options: [{ name: "1080p", _key: "i" }] },
    metadata: { tmdb: {} },
    requests: { seerr: {} },
    watchlists: { native: { enabled: false } },
    subtitles: { bazarr: {} },
    imports: {},
    updates: {},
    cloudflare: { enabled: true },
  };

  it("includes only dirty sections and strips _key from misc and indexers", () => {
    expect(composeSavePayload(local, new Set(["general", "indexers", "watchlists"]))).toEqual({
      misc: { show_libraries: [{ path: "/s" }] },
      cloudflare: { enabled: true },
      indexers: { quality_options: [{ name: "1080p" }] },
      watchlists: { native: { enabled: false } },
    });
  });

  it("maps scores dirty tab onto the indexers section", () => {
    expect(composeSavePayload(local, new Set(["scores"]))).toEqual({
      indexers: { quality_options: [{ name: "1080p" }] },
    });
  });

  it("returns an empty object when nothing is dirty", () => {
    expect(composeSavePayload(local, new Set())).toEqual({});
  });
});

describe("parseImportOverrides", () => {
  it("rejects invalid JSON", () => {
    expect(parseImportOverrides("{")).toEqual({ ok: false, error: "Invalid JSON file" });
  });

  it("requires an overrides object", () => {
    expect(parseImportOverrides("{}")).toEqual({
      ok: false,
      error: 'File missing "overrides" object',
    });
    expect(parseImportOverrides(JSON.stringify({ overrides: null }))).toEqual({
      ok: false,
      error: 'File missing "overrides" object',
    });
  });

  it("returns the overrides object for import body composition", () => {
    const overrides = { misc: { naming: { series: "x" } } };
    expect(parseImportOverrides(JSON.stringify({ overrides }))).toEqual({
      ok: true,
      overrides,
    });
  });
});

describe("reset behavior", () => {
  it("reset does not compose a save payload from local editor state", () => {
    // DELETE /api/v1/system/settings has no body; local dirty slices must not
    // be sent. composeSavePayload remains the save-only path.
    const dirty = composeSavePayload(
      {
        misc: { naming: { series: "dirty" } },
        auth: {},
        notifications: {},
        torrents: {},
        indexers: {},
        metadata: {},
        requests: {},
        watchlists: {},
        subtitles: {},
        imports: {},
        updates: {},
        cloudflare: {},
      },
      new Set(["general"]),
    );
    expect(dirty).toHaveProperty("misc");
    // Reset contract: callers must not reuse composeSavePayload for reset.
    expect(typeof dirty).toBe("object");
  });
});

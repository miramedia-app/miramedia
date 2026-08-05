import { describe, expect, it } from "vitest";

import {
  BUCKET_ORDER,
  KIND_LABELS,
  KIND_ORDER,
  apiTabFromBucketFilter,
  bucketOf,
  effectiveChoice,
  importsListViewState,
  rankedChoices,
} from "@/lib/imports";
import type {
  ImportItem,
  IntegrityImport,
  MediaImport,
  ScanCandidate,
  ScanImport,
  ScanProviderCandidate,
  StagedChoice,
  TorrentImport,
} from "@/lib/imports";

// Fixtures are typed against the generated API types, so a server schema change
// that invalidates them fails `pnpm run typecheck` rather than passing silently.

function torrent(
  progress: Partial<TorrentImport["entry"]["progress"]>,
  backoff: number | null = null,
): TorrentImport {
  return {
    kind: "torrent",
    id: "t1",
    backoff_seconds: backoff,
    entry: {
      torrent_id: "00000000-0000-0000-0000-000000000001",
      torrent_title: "Some.Release.1080p",
      torrent_status: 3,
      source_dir: "/data/downloads/some.release",
      progress: {
        total: 0,
        imported: 0,
        failed: 0,
        ambiguous: 0,
        pending: 0,
        ...progress,
      },
      files: [],
    },
  };
}

function scan(status: string): ScanImport {
  return {
    kind: "scan",
    id: "s1",
    result: {
      directory: "/data/tv/Some Show",
      detected_name: "Some Show",
      library_name: "tv",
      size_bytes: 1,
      file_count: 1,
      candidates: [],
      provider_candidates: [],
      files: [],
      status,
    },
  };
}

function media(): MediaImport {
  return {
    kind: "media",
    id: "m1",
    media_type: "movie",
    media_name: "Some Movie",
    torrent_title: "",
    source_dir: "",
    progress: { total: 1, imported: 1, failed: 0, ambiguous: 0, pending: 0 },
    files: [],
  };
}

function integrity(): IntegrityImport {
  return {
    kind: "integrity",
    id: "integrity:show:00000000-0000-0000-0000-0000000000aa",
    mismatch: {
      file_id: "00000000-0000-0000-0000-0000000000aa",
      media_type: "show",
      media_title: "Some Show",
      episode: "S01E01",
      path: "/data/tv/Some Show/Season 01/ep.mkv",
      quality: 2,
      variant_tag: "",
      import_error: "sha1 mismatch",
      detected_at: "2026-07-01T00:00:00Z",
    },
  };
}

describe("kind vocabulary and buckets", () => {
  it("labels every server kind and orders them", () => {
    expect(KIND_LABELS).toEqual({
      torrent: "Downloads",
      scan: "Scans",
      media: "Imported",
      integrity: "Integrity",
    });
    expect(KIND_ORDER).toEqual(["torrent", "scan", "media", "integrity"]);
  });

  it("orders buckets Review before Retry before Done", () => {
    expect(BUCKET_ORDER.Review).toBeLessThan(BUCKET_ORDER.Retry);
    expect(BUCKET_ORDER.Retry).toBeLessThan(BUCKET_ORDER.Done);
  });

  it("buckets a fully imported torrent as Done", () => {
    expect(bucketOf(torrent({ total: 3, imported: 3 }))).toBe("Done");
  });

  it("buckets a failed or ambiguous torrent as Review even when otherwise complete", () => {
    expect(bucketOf(torrent({ total: 3, imported: 3, failed: 1 }))).toBe("Review");
    expect(bucketOf(torrent({ total: 3, imported: 3, ambiguous: 1 }))).toBe("Review");
  });

  it("buckets a backing-off torrent as Retry, and one without backoff as Review", () => {
    expect(bucketOf(torrent({ total: 3, imported: 1 }, 120))).toBe("Retry");
    expect(bucketOf(torrent({ total: 3, imported: 1 }))).toBe("Review");
  });

  it("never calls an empty torrent Done", () => {
    expect(bucketOf(torrent({ total: 0, imported: 0 }))).toBe("Review");
  });

  it("buckets scans by result status, media as Done and integrity as Review", () => {
    expect(bucketOf(scan("imported"))).toBe("Done");
    expect(bucketOf(scan("pending"))).toBe("Review");
    expect(bucketOf(media())).toBe("Done");
    expect(bucketOf(integrity())).toBe("Review");
  });

  it("keeps every kind inside the label vocabulary", () => {
    const items: ImportItem[] = [torrent({}), scan("pending"), media(), integrity()];
    for (const it of items) expect(KIND_LABELS[it.kind]).toBeTruthy();
  });
});

describe("apiTabFromBucketFilter", () => {
  it("defaults to 'all' with no filter", () => {
    expect(apiTabFromBucketFilter(null)).toBe("all");
    expect(apiTabFromBucketFilter("")).toBe("all");
  });

  it("maps the first bucket value to its API tab", () => {
    expect(apiTabFromBucketFilter("bucket:Review")).toBe("review");
    expect(apiTabFromBucketFilter("bucket:Retry")).toBe("retry");
    expect(apiTabFromBucketFilter("bucket:Done")).toBe("done");
  });

  it("ignores non-bucket and negated segments and unknown values", () => {
    expect(apiTabFromBucketFilter("kind:torrent")).toBe("all");
    expect(apiTabFromBucketFilter("!bucket:Review")).toBe("all");
    expect(apiTabFromBucketFilter("bucket:Whatever")).toBe("all");
    expect(apiTabFromBucketFilter("kind:torrent&bucket:Done")).toBe("done");
  });

  it("uses only the first bucket value and trims before decoding", () => {
    expect(apiTabFromBucketFilter("bucket:Review,Done")).toBe("review");
    // Trim runs on the raw (pre-decode) value, so encoded spaces survive and
    // the value no longer matches a known bucket.
    expect(apiTabFromBucketFilter("bucket:%20Retry%20")).toBe("all");
  });
});

function candidate(over: Partial<ScanCandidate> = {}): ScanCandidate {
  return {
    media_type: "movie",
    media_id: "00000000-0000-0000-0000-0000000000c1",
    media_name: "Lib Movie",
    media_year: 2020,
    confidence: 0.5,
    breakdown: null,
    ...over,
  } as ScanCandidate;
}

function provider(over: Partial<ScanProviderCandidate> = {}): ScanProviderCandidate {
  return {
    media_type: "movie",
    external_id: "tt1",
    metadata_provider: "tmdb",
    name: "Provider Movie",
    year: 2021,
    confidence: 0.5,
    breakdown: null,
    ...over,
  } as ScanProviderCandidate;
}

function scanWith(
  candidates: ScanCandidate[],
  providers: ScanProviderCandidate[],
  status = "pending",
): ScanImport {
  const s = scan(status);
  s.result.candidates = candidates;
  s.result.provider_candidates = providers;
  return s;
}

describe("rankedChoices", () => {
  it("merges library and provider candidates sorted by descending confidence", () => {
    const s = scanWith(
      [candidate({ confidence: 0.3 }), candidate({ confidence: 0.9, media_id: "hi" })],
      [provider({ confidence: 0.6 })],
    );
    const ranked = rankedChoices(s.result);
    expect(ranked.map((r) => r.confidence)).toEqual([0.9, 0.6, 0.3]);
    expect(ranked[0].kind).toBe("candidate");
    expect(ranked[1].kind).toBe("provider");
  });

  it("treats missing confidence as 0 and returns [] for no candidates", () => {
    expect(rankedChoices(scanWith([], []).result)).toEqual([]);
    const s = scanWith([candidate({ confidence: undefined as unknown as number })], []);
    expect(rankedChoices(s.result)[0].confidence).toBe(0);
  });

  it("returns the same cached array for the same result reference", () => {
    const s = scanWith([candidate()], []);
    expect(rankedChoices(s.result)).toBe(rankedChoices(s.result));
  });
});

describe("effectiveChoice", () => {
  it("returns the staged choice verbatim when present", () => {
    const s = scanWith([candidate({ confidence: 0.9 })], []);
    const staged: StagedChoice = { kind: "provider", data: provider() };
    expect(effectiveChoice(s, staged)).toBe(staged);
  });

  it("falls back to the top-ranked candidate when nothing is staged", () => {
    const s = scanWith([candidate({ confidence: 0.2 })], [provider({ confidence: 0.8 })]);
    const eff = effectiveChoice(s, undefined);
    expect(eff).toEqual({ kind: "provider", data: provider({ confidence: 0.8 }) });
  });

  it("returns null when there is no candidate to fall back to", () => {
    expect(effectiveChoice(scanWith([], []), undefined)).toBeNull();
  });
});

describe("importsListViewState", () => {
  it("shows an error rather than a bogus 'no imports yet' empty state", () => {
    expect(importsListViewState({ isError: true, count: 0 })).toBe("error");
  });

  it("keeps rows we already hold usable through a failed refetch", () => {
    expect(importsListViewState({ isError: true, count: 3 })).toBe("list");
  });

  it("renders the list (empty state included) when the fetch succeeded", () => {
    expect(importsListViewState({ isError: false, count: 0 })).toBe("list");
    expect(importsListViewState({ isError: false, count: 2 })).toBe("list");
  });
});

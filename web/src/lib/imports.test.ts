import { describe, expect, it } from "vitest";

import {
  BUCKET_ORDER,
  KIND_LABELS,
  KIND_ORDER,
  bucketOf,
  importsListViewState,
} from "@/lib/imports";
import type {
  ImportItem,
  IntegrityImport,
  MediaImport,
  ScanImport,
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

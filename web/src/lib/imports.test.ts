import { describe, expect, it } from "vitest";

import {
  BUCKET_ORDER,
  KIND_LABELS,
  KIND_ORDER,
  MISMATCH_MEDIA_LABELS,
  MISMATCH_MEDIA_ORDER,
  bucketOf,
  canActOnMismatch,
  canQueryIntegrity,
  importsListViewState,
  integrityViewState,
  mismatchPageCounts,
  mismatchRowId,
  pageRangeLabel,
  previousOffset,
  shouldStepBack,
  visibleMismatches,
} from "@/lib/imports";
import type {
  ImportItem,
  IntegrityMismatch,
  IntegrityPage,
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

function mismatch(over: Partial<IntegrityMismatch> = {}): IntegrityMismatch {
  return {
    file_id: "00000000-0000-0000-0000-0000000000aa",
    media_type: "show",
    media_title: "Some Show",
    episode: "S01E01",
    path: "/data/tv/Some Show/Season 01/ep.mkv",
    quality: 2,
    variant_tag: "",
    import_error: "sha1 mismatch",
    detected_at: "2026-07-01T00:00:00Z",
    ...over,
  };
}

function page(items: IntegrityMismatch[], over: Partial<IntegrityPage> = {}): IntegrityPage {
  return { items, total: items.length, offset: 0, limit: 50, ...over };
}

describe("kind vocabulary and buckets", () => {
  it("labels every server kind and orders them", () => {
    expect(KIND_LABELS).toEqual({ torrent: "Downloads", scan: "Scans", media: "Imported" });
    expect(KIND_ORDER).toEqual(["torrent", "scan", "media"]);
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

  it("buckets scans by result status and media as Done", () => {
    expect(bucketOf(scan("imported"))).toBe("Done");
    expect(bucketOf(scan("pending"))).toBe("Review");
    expect(bucketOf(media())).toBe("Done");
  });

  it("keeps every kind inside the label vocabulary", () => {
    const items: ImportItem[] = [torrent({}), scan("pending"), media()];
    for (const it of items) expect(KIND_LABELS[it.kind]).toBeTruthy();
  });
});

describe("canQueryIntegrity", () => {
  it("stays false while the user query is still in flight", () => {
    expect(canQueryIntegrity({ userLoading: true, isSuperuser: undefined })).toBe(false);
    // Even a truthy cached role must not fire the privileged request unsettled.
    expect(canQueryIntegrity({ userLoading: true, isSuperuser: true })).toBe(false);
  });

  it("is false for a settled ordinary user", () => {
    expect(canQueryIntegrity({ userLoading: false, isSuperuser: false })).toBe(false);
    expect(canQueryIntegrity({ userLoading: false, isSuperuser: undefined })).toBe(false);
  });

  it("is true only for a settled superuser", () => {
    expect(canQueryIntegrity({ userLoading: false, isSuperuser: true })).toBe(true);
  });
});

describe("visibleMismatches", () => {
  const rows = [mismatch(), mismatch({ file_id: "bb", media_type: "movie" })];

  it("projects nothing for an unauthorized user even with a cached admin page", () => {
    expect(visibleMismatches({ page: page(rows), canSee: false, removed: new Set() })).toEqual([]);
  });

  it("returns the page rows for an authorized user", () => {
    expect(visibleMismatches({ page: page(rows), canSee: true, removed: new Set() })).toHaveLength(
      2,
    );
  });

  it("filters optimistically removed rows by row id", () => {
    const removed = new Set([mismatchRowId(rows[0]!)]);
    const out = visibleMismatches({ page: page(rows), canSee: true, removed });
    expect(out.map(mismatchRowId)).toEqual([mismatchRowId(rows[1]!)]);
  });

  it("returns nothing with no page", () => {
    expect(visibleMismatches({ page: undefined, canSee: true, removed: new Set() })).toEqual([]);
  });
});

describe("mismatchRowId", () => {
  it("namespaces by media type so a show and movie file id cannot collide", () => {
    expect(mismatchRowId(mismatch({ file_id: "x" }))).toBe("corrupt:show:x");
    expect(mismatchRowId(mismatch({ file_id: "x", media_type: "movie" }))).toBe("corrupt:movie:x");
  });
});

describe("canActOnMismatch", () => {
  it("requires authorization and no in-flight action", () => {
    expect(canActOnMismatch({ canSee: true, busy: false })).toBe(true);
    expect(canActOnMismatch({ canSee: true, busy: true })).toBe(false);
    expect(canActOnMismatch({ canSee: false, busy: false })).toBe(false);
  });
});

describe("paging helpers", () => {
  it("clamps previousOffset at zero", () => {
    expect(previousOffset(0, 50)).toBe(0);
    expect(previousOffset(20, 50)).toBe(0);
    expect(previousOffset(100, 50)).toBe(50);
  });

  it("labels the range from the rendered page offset", () => {
    expect(pageRangeLabel({ offset: 0, count: 50, total: 213 })).toBe("1–50 of 213");
    expect(pageRangeLabel({ offset: 50, count: 50, total: 213 })).toBe("51–100 of 213");
  });

  it("shrinks the range when a row is optimistically removed", () => {
    expect(pageRangeLabel({ offset: 50, count: 49, total: 212 })).toBe("51–99 of 212");
  });

  it("labels an empty page without a bogus range", () => {
    expect(pageRangeLabel({ offset: 0, count: 0, total: 0 })).toBe("0 of 0");
  });

  it("steps back only from a non-zero, empty page", () => {
    expect(shouldStepBack({ offset: 50, count: 0 })).toBe(true);
    expect(shouldStepBack({ offset: 50, count: 1 })).toBe(false);
    // Never at offset 0 — otherwise the step-back effect would loop.
    expect(shouldStepBack({ offset: 0, count: 0 })).toBe(false);
  });
});

describe("mismatchPageCounts", () => {
  it("counts the rendered page only and always names both media types", () => {
    expect(mismatchPageCounts([])).toEqual({ show: 0, movie: 0 });
    const rows = [mismatch(), mismatch({ file_id: "b" }), mismatch({ media_type: "movie" })];
    expect(mismatchPageCounts(rows)).toEqual({ show: 2, movie: 1 });
  });

  it("labels and orders the media facets", () => {
    expect(MISMATCH_MEDIA_ORDER).toEqual(["show", "movie"]);
    expect(MISMATCH_MEDIA_LABELS).toEqual({ show: "Episodes", movie: "Movies" });
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

describe("integrityViewState", () => {
  it("hides the whole section from an unauthorized user, error or not", () => {
    expect(integrityViewState({ canSee: false, isError: true, isPending: false, count: 3 })).toBe(
      "hidden",
    );
  });

  it("shows an error independent of the rest of the imports page", () => {
    expect(integrityViewState({ canSee: true, isError: true, isPending: true, count: 0 })).toBe(
      "error",
    );
  });

  it("distinguishes pending, empty and populated pages", () => {
    expect(integrityViewState({ canSee: true, isError: false, isPending: true, count: 0 })).toBe(
      "pending",
    );
    expect(integrityViewState({ canSee: true, isError: false, isPending: false, count: 0 })).toBe(
      "empty",
    );
    expect(integrityViewState({ canSee: true, isError: false, isPending: false, count: 2 })).toBe(
      "rows",
    );
  });
});

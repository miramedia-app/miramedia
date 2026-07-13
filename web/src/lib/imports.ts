import type { components } from "@/lib/api/api";

export type TorrentImport = components["schemas"]["TorrentImportItem"];
export type ScanImport = components["schemas"]["ScanImportItem"];
export type MediaImport = components["schemas"]["MediaImportItem"];
export type IntegrityMismatch = components["schemas"]["IntegrityMismatch"];
export type IntegrityPage = components["schemas"]["PaginatedIntegrityMismatches"];

export type ImportItem = TorrentImport | ScanImport | MediaImport;

export type ImportBucket = "Review" | "Retry" | "Done";

export const BUCKET_ORDER: Record<ImportBucket, number> = {
  Review: 0,
  Retry: 1,
  Done: 2,
};

/**
 * The single kind vocabulary shared by grouping, facets and counts. Typed as an
 * exhaustive `Record` over `ImportItem["kind"]`, so adding a server kind fails
 * the typecheck here instead of silently falling into the "Downloads" bucket.
 */
export const KIND_LABELS: Record<ImportItem["kind"], string> = {
  torrent: "Downloads",
  scan: "Scans",
  media: "Imported",
};

export const KIND_ORDER: ImportItem["kind"][] = ["torrent", "scan", "media"];

export function isTorrent(item: ImportItem): item is TorrentImport {
  return item.kind === "torrent";
}

export function isMedia(item: ImportItem): item is MediaImport {
  return item.kind === "media";
}

export function bucketOf(it: ImportItem): ImportBucket {
  if (it.kind === "scan") return it.result.status === "imported" ? "Done" : "Review";
  if (it.kind === "media") return "Done";
  const p = it.entry.progress;
  if ((p.failed ?? 0) > 0 || (p.ambiguous ?? 0) > 0) return "Review";
  if (p.imported >= p.total && p.total > 0) return "Done";
  if (it.backoff_seconds != null) return "Retry";
  return "Review";
}

// ---------------------------------------------------------------------------
// File integrity (corruption) — its own server-paginated, superuser-only
// section. Deliberately NOT folded into the import list above: the list is an
// in-memory projection with global filters/sorts, and one server page of
// mismatches cannot honour those claims.
// ---------------------------------------------------------------------------

/**
 * Rows requested per integrity page. Matches the server default
 * (`INTEGRITY_MISMATCH_DEFAULT_LIMIT`; the endpoint caps at 100) — each row
 * costs a disk-path resolution, so stay conservative.
 */
export const INTEGRITY_PAGE_SIZE = 50;

/** Corruption vocabulary, kept explicit and separate from the import kinds. */
export const CORRUPT_LABEL = "Corrupt";
export const MISMATCH_MEDIA_LABELS: Record<IntegrityMismatch["media_type"], string> = {
  show: "Episodes",
  movie: "Movies",
};
export const MISMATCH_MEDIA_ORDER: IntegrityMismatch["media_type"][] = ["show", "movie"];

export function mismatchRowId(m: IntegrityMismatch): string {
  return `corrupt:${m.media_type}:${m.file_id}`;
}

/**
 * Whether the privileged integrity query may run. Requires the user query to be
 * *settled*: while it is in flight `user` is null, and an ordinary user must
 * never fire the request — nor flash its rows/controls — on an optimistic guess.
 */
export function canQueryIntegrity(args: {
  userLoading: boolean;
  isSuperuser: boolean | undefined;
}): boolean {
  return !args.userLoading && args.isSuperuser === true;
}

/**
 * Project the visible rows of the currently fetched page.
 *
 * `canSee` is the *current* authorization state, not a hint: when false no row
 * is produced even if React Query still holds the page an admin session fetched
 * (a disabled query keeps serving its last successful data, so an ordinary user
 * signing in after an admin in the same SPA would otherwise inherit its rows).
 */
export function visibleMismatches(args: {
  page: IntegrityPage | undefined;
  canSee: boolean;
  removed: ReadonlySet<string>;
}): IntegrityMismatch[] {
  if (!args.canSee || !args.page) return [];
  return args.page.items.filter((m) => !args.removed.has(mismatchRowId(m)));
}

/** Whether a privileged per-row action may render and fire. */
export function canActOnMismatch(args: { canSee: boolean; busy: boolean }): boolean {
  return args.canSee && !args.busy;
}

/** Previous-page offset, clamped at zero. */
export function previousOffset(offset: number, pageSize: number): number {
  return Math.max(0, offset - pageSize);
}

/**
 * Accessible range for the fetched page, e.g. "1–50 of 213". `count` is the
 * number of rows actually rendered, so an optimistically-removed row shrinks
 * the range. Describes the fetched page only — it claims nothing about rows on
 * pages that were never fetched.
 */
export function pageRangeLabel(args: { offset: number; count: number; total: number }): string {
  if (args.count === 0) return `0 of ${args.total}`;
  const first = args.offset + 1;
  const last = args.offset + args.count;
  return `${first}–${last} of ${args.total}`;
}

/**
 * A non-zero page that came back (or was emptied) with no rows is out of range:
 * step back exactly one page. Never true at offset 0, so it cannot loop.
 */
export function shouldStepBack(args: { offset: number; count: number }): boolean {
  return args.offset > 0 && args.count === 0;
}

/** Counts by media type for the fetched page. Explicitly page-scoped, not global. */
export function mismatchPageCounts(
  rows: IntegrityMismatch[],
): Record<IntegrityMismatch["media_type"], number> {
  const counts: Record<IntegrityMismatch["media_type"], number> = { show: 0, movie: 0 };
  for (const m of rows) counts[m.media_type] += 1;
  return counts;
}

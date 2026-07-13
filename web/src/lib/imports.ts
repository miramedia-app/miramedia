import type { components } from "@/lib/api/api";

export type TorrentImport = components["schemas"]["TorrentImportItem"];
export type ScanImport = components["schemas"]["ScanImportItem"];
export type MediaImport = components["schemas"]["MediaImportItem"];
export type IntegrityMismatch = components["schemas"]["IntegrityMismatch"];

/** Integrity-audit mismatch (bit-rot) folded into the imports list as a row. */
export type CorruptImport = { kind: "corrupt"; id: string; mismatch: IntegrityMismatch };

export type ImportItem = TorrentImport | ScanImport | MediaImport | CorruptImport;

export type ImportBucket = "Review" | "Retry" | "Corrupt" | "Done";

export const BUCKET_ORDER: Record<ImportBucket, number> = {
  Review: 0,
  Retry: 1,
  Corrupt: 2,
  Done: 3,
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
  corrupt: "Corrupt",
};

export const KIND_ORDER: ImportItem["kind"][] = ["torrent", "scan", "media", "corrupt"];

export function corruptRowId(m: IntegrityMismatch): string {
  return `corrupt:${m.media_type}:${m.file_id}`;
}

export function isTorrent(item: ImportItem): item is TorrentImport {
  return item.kind === "torrent";
}

export function isMedia(item: ImportItem): item is MediaImport {
  return item.kind === "media";
}

export function isCorrupt(item: ImportItem): item is CorruptImport {
  return item.kind === "corrupt";
}

export function bucketOf(it: ImportItem): ImportBucket {
  if (it.kind === "corrupt") return "Corrupt";
  if (it.kind === "scan") return it.result.status === "imported" ? "Done" : "Review";
  if (it.kind === "media") return "Done";
  const p = it.entry.progress;
  if ((p.failed ?? 0) > 0 || (p.ambiguous ?? 0) > 0) return "Review";
  if (p.imported >= p.total && p.total > 0) return "Done";
  if (it.backoff_seconds != null) return "Retry";
  return "Review";
}

/**
 * Project the visible import rows.
 *
 * `canSeeIntegrity` is the *current* authorization state, not a hint: when it is
 * false no corruption row is produced even if `mismatches` still holds data a
 * previously-authorized (admin) session left in the cache. Gating only React
 * Query's `enabled` is not enough — a disabled query keeps serving its last
 * successful data, so an ordinary user logging in after an admin in the same SPA
 * would otherwise still see the admin's corruption rows.
 */
export function buildImportItems({
  listItems,
  mismatches,
  canSeeIntegrity,
  removedCorrupt,
}: {
  listItems: ImportItem[];
  mismatches: IntegrityMismatch[];
  canSeeIntegrity: boolean;
  removedCorrupt: ReadonlySet<string>;
}): ImportItem[] {
  if (!canSeeIntegrity) return [...listItems];
  const corrupt: CorruptImport[] = mismatches
    .map((m): CorruptImport => ({ kind: "corrupt", id: corruptRowId(m), mismatch: m }))
    .filter((c) => !removedCorrupt.has(c.id));
  return [...listItems, ...corrupt];
}

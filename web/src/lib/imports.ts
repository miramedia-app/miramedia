import type { components } from "@/lib/api/api";

export type TorrentImport = components["schemas"]["TorrentImportItem"];
export type ScanImport = components["schemas"]["ScanImportItem"];
export type MediaImport = components["schemas"]["MediaImportItem"];
export type IntegrityImport = components["schemas"]["IntegrityImportItem"];
export type IntegrityMismatch = components["schemas"]["IntegrityMismatch"];

export type ImportItem = TorrentImport | ScanImport | MediaImport | IntegrityImport;

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
  integrity: "Integrity",
};

export const KIND_ORDER: ImportItem["kind"][] = ["torrent", "scan", "media", "integrity"];

/** Row status pill label for corrupt (integrity-mismatch) files. */
export const CORRUPT_LABEL = "Corrupt";

export function isTorrent(item: ImportItem): item is TorrentImport {
  return item.kind === "torrent";
}

export function isMedia(item: ImportItem): item is MediaImport {
  return item.kind === "media";
}

export function isIntegrity(item: ImportItem): item is IntegrityImport {
  return item.kind === "integrity";
}

export function bucketOf(it: ImportItem): ImportBucket {
  if (it.kind === "scan") return it.result.status === "imported" ? "Done" : "Review";
  if (it.kind === "media") return "Done";
  // A corrupt file needs a human decision (accept current / dismiss).
  if (it.kind === "integrity") return "Review";
  const p = it.entry.progress;
  if ((p.failed ?? 0) > 0 || (p.ambiguous ?? 0) > 0) return "Review";
  if (p.imported >= p.total && p.total > 0) return "Done";
  if (it.backoff_seconds != null) return "Retry";
  return "Review";
}

/**
 * What the imports list renders. A failed fetch with nothing cached must not be
 * dressed up as "No imports yet" — an empty library and an unreachable API are
 * different facts. Rows we already hold survive a failed refetch: the list stays
 * usable and the staleness is the lesser evil.
 */
export function importsListViewState(args: { isError: boolean; count: number }): "error" | "list" {
  return args.isError && args.count === 0 ? "error" : "list";
}

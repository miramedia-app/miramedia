import type { components } from "@/lib/api/api";

export type TorrentImport = components["schemas"]["TorrentImportItem"];
export type ScanImport = components["schemas"]["ScanImportItem"];
export type MediaImport = components["schemas"]["MediaImportItem"];
export type IntegrityImport = components["schemas"]["IntegrityImportItem"];
export type IntegrityMismatch = components["schemas"]["IntegrityMismatch"];
export type ScanCandidate = components["schemas"]["ScanCandidate"];
export type ScanProviderCandidate = components["schemas"]["ScanProviderCandidate"];

export type ImportItem = TorrentImport | ScanImport | MediaImport | IntegrityImport;

/** Imports API `tab` query values. */
export type ImportTabApi = "all" | "review" | "retry" | "done";

/** Map search-bar Status facet (URL ``f`` param) to the imports API tab. */
export function apiTabFromBucketFilter(filterParam: string | null): ImportTabApi {
  if (!filterParam) return "all";
  for (const segment of filterParam.split("&")) {
    if (!segment || segment.startsWith("!")) continue;
    const [facetId, rawValues = ""] = segment.split(":");
    if (facetId !== "bucket") continue;
    const value = decodeURIComponent(rawValues.split(",")[0]?.trim() ?? "");
    if (value === "Review") return "review";
    if (value === "Retry") return "retry";
    if (value === "Done") return "done";
    return "all";
  }
  return "all";
}

export type RankedChoice =
  | { kind: "candidate"; data: ScanCandidate; confidence: number }
  | { kind: "provider"; data: ScanProviderCandidate; confidence: number };

/** A destination the user staged for a scan row (no computed confidence). */
export type StagedChoice =
  | { kind: "candidate"; data: ScanCandidate }
  | { kind: "provider"; data: ScanProviderCandidate };

// Memoize per scan-result reference. Same object identity (until a refetch
// replaces it) reuses the previous ranked list, avoiding the two-loop +
// sort each time the row renders (destination column + row actions).
const rankedCache = new WeakMap<ScanImport["result"], RankedChoice[]>();

/** Library + provider candidates for a scan result, merged and confidence-sorted. */
export function rankedChoices(r: ScanImport["result"]): RankedChoice[] {
  const cached = rankedCache.get(r);
  if (cached) return cached;
  const out: RankedChoice[] = [];
  for (const c of r.candidates ?? []) {
    out.push({ kind: "candidate", data: c, confidence: c.confidence ?? 0 });
  }
  for (const c of r.provider_candidates ?? []) {
    out.push({ kind: "provider", data: c, confidence: c.confidence ?? 0 });
  }
  out.sort((a, b) => b.confidence - a.confidence);
  rankedCache.set(r, out);
  return out;
}

/**
 * The destination a scan row will import into: an explicitly-staged choice if
 * the user picked one, otherwise the highest-confidence candidate.
 */
export function effectiveChoice(
  item: ScanImport,
  staged: StagedChoice | undefined,
): StagedChoice | null {
  if (staged) return staged;
  const top = rankedChoices(item.result)[0];
  if (!top) return null;
  return top.kind === "candidate"
    ? { kind: "candidate", data: top.data }
    : { kind: "provider", data: top.data };
}

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

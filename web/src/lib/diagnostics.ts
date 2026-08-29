/**
 * Diagnostics is a read-only operator view. Storage unknown must stay unknown —
 * never coerce failed loads or unhashed files to healthy/zero.
 */

export const STORAGE_HEALTH_FORBIDDEN_ACTIONS = [
  "rebaseline",
  "dismiss",
  "delete",
  "reimport",
  "reacquire",
] as const;

export const DIAGNOSTICS_ERROR_MESSAGE =
  "Unable to load diagnostics. Check that the server is reachable and try again.";

export const STORAGE_HEALTH_ERROR_MESSAGE =
  "Unable to load storage diagnostics. Check that the server is reachable and try again.";

export const STORAGE_HEALTH_COPY = {
  missingNote:
    "Missing files are shown on title pages and in row detail when a directory is readable.",
  integrityOff:
    "Integrity audit is off. Imported files are not hashed; this is not a failure count.",
  integrityOnUnknown: "Not yet hashed. The next audit pass will baseline or verify these files.",
  allClear: "No imported file issues in the database. On-disk missing is not counted here.",
  inaccessible: "This library root is inaccessible. Files were not marked missing.",
  hashingDisabled: "Hashing disabled — imported files have no SHA1.",
} as const;

export const DIAGNOSTICS_TABS = ["storage", "database", "scheduler"] as const;
export type DiagnosticsTab = (typeof DIAGNOSTICS_TABS)[number];

export type StorageHealthSqlState = "corrupt" | "unknown" | "orphaned" | "pending" | "healthy";

export type StorageHealthListParams = {
  tab: DiagnosticsTab;
  page: number;
  pageSize: number;
  offset: number;
  limit: number;
  state?: StorageHealthSqlState;
  mediaType?: "show" | "movie";
  q?: string;
  detailMediaType?: "show" | "movie";
  detailFileId?: string;
};

const SQL_STATES: ReadonlySet<string> = new Set([
  "corrupt",
  "unknown",
  "orphaned",
  "pending",
  "healthy",
]);

const TABS: ReadonlySet<string> = new Set(DIAGNOSTICS_TABS);

export function parseDiagnosticsTab(params: { get(name: string): string | null }): DiagnosticsTab {
  const tab = params.get("tab") ?? "storage";
  return TABS.has(tab) ? (tab as DiagnosticsTab) : "storage";
}

export function parseStorageHealthSearch(params: {
  get(name: string): string | null;
}): StorageHealthListParams {
  const pageRaw = params.get("p");
  const psRaw = params.get("ps");
  const page = Math.max(1, Number.parseInt(pageRaw ?? "1", 10) || 1);
  const pageSize = Math.min(100, Math.max(1, Number.parseInt(psRaw ?? "50", 10) || 50));
  const q = (params.get("q") ?? "").trim() || undefined;
  const filters = params.get("f") ?? "";
  let state: StorageHealthSqlState | undefined;
  let mediaType: "show" | "movie" | undefined;
  for (const segment of filters.split("&")) {
    if (!segment || segment.startsWith("!")) continue;
    const [facetId, rawValues = ""] = segment.split(":");
    const value = decodeURIComponent(rawValues.split(",")[0]?.trim() ?? "");
    if (facetId === "state" && SQL_STATES.has(value)) {
      state = value as StorageHealthSqlState;
    }
    if (facetId === "media_type" && (value === "show" || value === "movie")) {
      mediaType = value;
    }
  }
  const mt = params.get("mt");
  const fid = params.get("fid");
  return {
    tab: parseDiagnosticsTab(params),
    page,
    pageSize,
    offset: (page - 1) * pageSize,
    limit: pageSize,
    state,
    mediaType,
    q,
    detailMediaType: mt === "show" || mt === "movie" ? mt : undefined,
    detailFileId: fid && fid.length > 0 ? fid : undefined,
  };
}

export function storageHealthFilterParam(args: {
  state?: StorageHealthSqlState;
  mediaType?: "show" | "movie";
}): string {
  const parts: string[] = [];
  if (args.state) parts.push(`state:${args.state}`);
  if (args.mediaType) parts.push(`media_type:${args.mediaType}`);
  return parts.join("&");
}

export type StorageHealthCountsView = {
  imported: number;
  healthy: number;
  unknown: number;
  corrupt: number;
  orphaned: number;
  pending: number;
  missing: null;
};

export type StorageHealthView =
  | { status: "pending" }
  | { status: "error"; message: string }
  | { status: "success"; counts: StorageHealthCountsView };

export function storageHealthViewState(args: {
  isPending: boolean;
  isError: boolean;
  data: (Omit<StorageHealthCountsView, "missing"> & { missing?: null }) | null | undefined;
}): StorageHealthView {
  if (args.data != null) {
    return { status: "success", counts: { ...args.data, missing: null } };
  }
  if (args.isError) {
    return { status: "error", message: STORAGE_HEALTH_ERROR_MESSAGE };
  }
  return { status: "pending" };
}

export function storageHealthUnknownHint(args: {
  integrityEnabled: boolean;
  unknown: number;
}): string | null {
  if (args.unknown <= 0) return null;
  return args.integrityEnabled
    ? STORAGE_HEALTH_COPY.integrityOnUnknown
    : STORAGE_HEALTH_COPY.integrityOff;
}

export function storageHealthTitleHref(mediaType: "show" | "movie", mediaId: string): string {
  return mediaType === "show" ? `/dashboard/shows/${mediaId}` : `/dashboard/movies/${mediaId}`;
}

export function storageHealthImportsHref(): string {
  return "/dashboard/imports";
}

export function storageHealthHasMutationControls(haystack: string): boolean {
  const lower = haystack.toLowerCase();
  return STORAGE_HEALTH_FORBIDDEN_ACTIONS.some((action) => lower.includes(action));
}

export function storageHealthAllClear(counts: StorageHealthCountsView): boolean {
  return (
    counts.corrupt === 0 && counts.orphaned === 0 && counts.pending === 0 && counts.unknown === 0
  );
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  const value = i === 0 || n >= 10 ? String(Math.round(n)) : n.toFixed(1);
  return `${value} ${units[i]}`;
}

export function volumeUsedPercent(args: {
  used_bytes?: number | null;
  total_bytes?: number | null;
}): number | null {
  if (
    args.used_bytes == null ||
    args.total_bytes == null ||
    args.total_bytes <= 0 ||
    args.used_bytes < 0
  ) {
    return null;
  }
  return Math.min(100, Math.max(0, (args.used_bytes / args.total_bytes) * 100));
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function humanizeCron(cron: string | null | undefined): string {
  if (!cron) return "—";
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
  if (dayOfMonth === "*" && month === "*") {
    if (hour === "*" && (minute === "*" || minute === "*/1")) return "Every minute";
    if (hour === "*" && minute.startsWith("*/")) {
      const n = Number(minute.slice(2));
      if (Number.isFinite(n) && n > 0) return n === 1 ? "Every minute" : `Every ${n} minutes`;
    }
    if (minute === "0" && hour === "*") return "Hourly";
    if (minute === "0" && hour.startsWith("*/")) {
      const n = Number(hour.slice(2));
      if (Number.isFinite(n) && n > 0) return n === 1 ? "Hourly" : `Every ${n} hours`;
    }
    if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && dayOfWeek === "*") {
      return `Daily at ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
    }
    if (/^\d+$/.test(minute) && /^\d+$/.test(hour) && /^\d+$/.test(dayOfWeek)) {
      const day = WEEKDAYS[Number(dayOfWeek)];
      if (day) {
        return `Weekly on ${day} at ${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
      }
    }
  }
  return cron;
}

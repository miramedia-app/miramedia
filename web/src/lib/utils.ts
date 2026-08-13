import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { components } from "@/lib/api/api";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const qualityMap: Record<number, string> = {
  1: "4K",
  2: "1080p",
  3: "720p",
  4: "480p",
  5: "unknown",
};

export const torrentStatusMap: Record<number, string> = {
  1: "Finished",
  2: "Downloading",
  3: "Paused",
  4: "Failed",
  5: "Unknown",
};

export function getTorrentQualityString(value: number): string {
  return qualityMap[value] || "unknown";
}

export function getTorrentStatusString(value: number): string {
  return torrentStatusMap[value] || "unknown";
}

export function getFullyQualifiedMediaName(media: { name: string; year: number | null }): string {
  let name = media.name;
  if (media.year != null) name += " (" + media.year + ")";
  return name;
}

const NAMED_HTML_ENTITIES: Record<string, string> = {
  amp: "&",
  apos: "'",
  quot: '"',
  lt: "<",
  gt: ">",
  nbsp: " ",
};

export function unescapeHtmlEntities(value: string): string {
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, entity: string) => {
    const lower = entity.toLowerCase();
    if (lower.startsWith("#x")) {
      const code = Number.parseInt(lower.slice(2), 16);
      return Number.isFinite(code) ? String.fromCodePoint(code) : match;
    }
    if (lower.startsWith("#")) {
      const code = Number.parseInt(lower.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : match;
    }
    return NAMED_HTML_ENTITIES[lower] ?? match;
  });
}

export function formatCastLine(cast: string[]): string {
  return cast.map(unescapeHtmlEntities).join(", ");
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function convertTorrentSeasonRangeToIntegerRange(seasons: number[]): string {
  if (seasons.length === 1) return pad2(seasons[0]!);
  if (seasons.length > 1) return pad2(seasons[0]!) + "-" + pad2(seasons.at(-1)!);
  return "";
}

export function convertTorrentEpisodeRangeToIntegerRange(episodes: number[]): string {
  if (episodes.length === 1) return pad2(episodes[0]!);
  if (episodes.length > 1) return pad2(episodes[0]!) + "-" + pad2(episodes.at(-1)!);
  return "";
}

export function formatTorrentSeasonEpisodeLabel(seasons: number[], episodes: number[]): string {
  let label = "";
  if (seasons.length > 0) label += "S" + convertTorrentSeasonRangeToIntegerRange(seasons);
  if (episodes.length > 0) label += "E" + convertTorrentEpisodeRangeToIntegerRange(episodes);
  return label;
}

export function formatSecondsToOptimalUnit(seconds: number): string {
  if (seconds < 0) return "0s";
  const units = [
    { name: "y", seconds: 365.25 * 24 * 60 * 60 },
    { name: "mo", seconds: 30.44 * 24 * 60 * 60 },
    { name: "d", seconds: 24 * 60 * 60 },
    { name: "h", seconds: 60 * 60 },
    { name: "m", seconds: 60 },
    { name: "s", seconds: 1 },
  ];
  for (const unit of units) {
    const value = seconds / unit.seconds;
    if (value >= 1) return `${Math.floor(value)}${unit.name}`;
  }
  return "0s";
}

export function saveDirectoryPreview(
  media: components["schemas"]["Show"] | components["schemas"]["Movie"],
  suffix: string = "",
) {
  let path =
    "/" +
    getFullyQualifiedMediaName(media) +
    " [" +
    media.metadata_provider +
    "id-" +
    media.external_id +
    "]/";
  if ("seasons" in media) {
    path += " Season XX/SXXEXX" + (suffix === "" ? "" : " - " + suffix) + ".mkv";
  } else {
    path += media.name + (suffix === "" ? "" : " - " + suffix) + ".mkv";
  }
  return path;
}

const QUALITY_LABEL: Record<string, string> = {
  uhd: "2160p",
  fullhd: "1080p",
  hd: "720p",
  sd: "480p",
  unknown: "",
};

const NUMERIC_QUALITY: Record<number, string> = {
  1: "uhd",
  2: "fullhd",
  3: "hd",
  4: "sd",
  5: "unknown",
};

/**
 * Render the pre-formatted display suffix for a saved media file, mirroring the
 * backend ``quality_naming.file_suffix`` helper. Combines the quality label with
 * the bracketed ``codec-variant-extra`` components (joined with ``-``, skipping
 * empty parts; HDR/source are stored but intentionally not shown here). Produces
 * ``"1080p"``, ``"1080p [h265]"``, ``"1080p [h265-director-cut-2]"``,
 * ``"[director-cut]"``, or ``""``.
 */
export function formatFileSuffix(file: {
  quality: number | string | null | undefined;
  codec?: string | null;
  variant?: string | null;
  extra?: string | null;
}): string {
  const key =
    typeof file.quality === "number" ? NUMERIC_QUALITY[file.quality] : (file.quality ?? "unknown");
  const label = QUALITY_LABEL[key ?? "unknown"] ?? "";
  const inner = [file.codec, file.variant, file.extra]
    .map((p) => (p ?? "").trim())
    .filter((p) => p)
    .join("-");
  if (label && inner) return `${label} [${inner}]`;
  if (label) return label;
  if (inner) return `[${inner}]`;
  return "";
}

export function qualityToString(
  q: number | string | null | undefined,
): "uhd" | "fullhd" | "hd" | "sd" | "unknown" {
  if (typeof q === "number")
    return (NUMERIC_QUALITY[q] as "uhd" | "fullhd" | "hd" | "sd" | "unknown") ?? "unknown";
  return (q as "uhd" | "fullhd" | "hd" | "sd" | "unknown") ?? "unknown";
}

const STRING_QUALITY: Record<string, 1 | 2 | 3 | 4 | 5> = {
  uhd: 1,
  fullhd: 2,
  hd: 3,
  sd: 4,
  unknown: 5,
};

export function qualityToNumber(q: string | number | null | undefined): 1 | 2 | 3 | 4 | 5 | null {
  if (q == null) return null;
  if (typeof q === "number") return q as 1 | 2 | 3 | 4 | 5;
  return STRING_QUALITY[q] ?? null;
}

export function semverIsGreater(a: string, b: string) {
  return a.localeCompare(b, undefined, { numeric: true }) === 1;
}

/**
 * Cross-context clipboard write. ``navigator.clipboard.writeText`` only works
 * over HTTPS or localhost; on plain HTTP origins (self-hosted NAS, LAN access)
 * the API is undefined and silently fails. Falls back to the legacy
 * ``document.execCommand("copy")`` via a hidden textarea so copy buttons still
 * work in insecure contexts.
 */
export async function copyToClipboard(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall through to legacy path — some browsers reject even in
      // secure contexts when focus isn't on a user-initiated event.
    }
  }
  if (typeof document === "undefined") {
    throw new Error("Clipboard unavailable: no document");
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } finally {
    document.body.removeChild(ta);
  }
  if (!ok) throw new Error("Clipboard write failed");
}

const SEMVER_RE =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;

export function isSemver(str: string): boolean {
  return SEMVER_RE.test(str);
}

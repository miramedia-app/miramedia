"use client";

import * as React from "react";

import { StatusPill } from "@/components/ui/status-pill";
import { MetaPill, TypePill } from "@/components/ui/type-pill";
import { MatchConfidencePill } from "@/components/match-confidence-pill";
import type { ColumnDef, FacetDef, GroupByDef } from "@/components/data-list";
import { getTorrentStatusString, qualityToString } from "@/lib/utils";
import {
  BUCKET_ORDER,
  CORRUPT_LABEL,
  KIND_LABELS,
  KIND_ORDER,
  bucketOf,
  isIntegrity,
  isMedia,
  isTorrent,
  rankedChoices,
} from "@/lib/imports";
import type { ImportItem, StagedChoice } from "@/lib/imports";

const TRAILING_SLASHES = /\/+$/;

export interface BuildImportColumnsOptions {
  stagedByScan: Record<string, StagedChoice>;
  onChooseDestination: (item: ImportItem) => void;
}

/**
 * DataList column definitions for the imports table. `stagedByScan` and the
 * destination-picker callback are the only page state the columns close over.
 */
export function buildImportColumns({
  stagedByScan,
  onChooseDestination,
}: BuildImportColumnsOptions): ColumnDef<ImportItem>[] {
  return [
    {
      id: "source",
      header: "Source",
      width: "minmax(240px,2fr)",
      render: (it) => {
        // Imported (cleaned-up) rows have no live source dir; show the
        // original torrent release name preserved in torrent_history.
        if (isMedia(it) && it.torrent_title) {
          return (
            <span
              className="truncate pr-3 font-mono text-xs text-muted-foreground"
              title={it.torrent_title}
            >
              {it.torrent_title}
            </span>
          );
        }
        if (isIntegrity(it)) {
          // The corrupt file itself, not a source directory.
          const file = it.mismatch.path?.split("/").filter(Boolean).pop() ?? "—";
          return (
            <span
              className="truncate pr-3 font-mono text-xs text-muted-foreground"
              title={it.mismatch.path ?? undefined}
            >
              {file}
            </span>
          );
        }
        const folder = isTorrent(it)
          ? it.entry.source_dir
          : isMedia(it)
            ? it.source_dir
            : it.result.directory;
        const name = folder?.replace(TRAILING_SLASHES, "").split("/").filter(Boolean).pop() ?? "";
        return (
          <span className="truncate pr-3 font-mono text-xs text-muted-foreground" title={folder}>
            {name || "—"}
          </span>
        );
      },
    },
    {
      id: "destination",
      header: "Destination",
      width: "minmax(240px,2fr)",
      render: (it) => {
        if (isTorrent(it)) {
          const m = it.entry.media;
          return (
            <span className="truncate pr-3 text-sm">
              {m?.media_name ? (
                <>
                  {m.media_name}
                  {m.media_year ? ` (${m.media_year})` : ""}
                </>
              ) : (
                <span className="text-muted-foreground">Unlinked</span>
              )}
            </span>
          );
        }
        if (isMedia(it)) {
          return (
            <span className="truncate pr-3 text-sm">
              {it.media_name}
              {it.media_year ? ` (${it.media_year})` : ""}
            </span>
          );
        }
        if (isIntegrity(it)) {
          return (
            <span className="truncate pr-3 text-sm">
              {it.mismatch.media_title}
              {it.mismatch.episode ? (
                <span className="ml-1.5 font-mono text-xs text-muted-foreground">
                  {it.mismatch.episode}
                </span>
              ) : null}
            </span>
          );
        }
        const r = it.result;
        if (r.imported_name) {
          return <span className="truncate pr-3 text-sm">{r.imported_name}</span>;
        }
        const staged = stagedByScan[it.id];
        const ranked = rankedChoices(r);
        const top = ranked[0];
        let label: string;
        let conf: number | null;
        if (staged?.kind === "candidate") {
          label = `${staged.data.media_name}${staged.data.media_year ? ` (${staged.data.media_year})` : ""}`;
          conf = staged.data.confidence;
        } else if (staged?.kind === "provider") {
          label = `${staged.data.name}${staged.data.year ? ` (${staged.data.year})` : ""}`;
          conf = staged.data.confidence;
        } else if (top?.kind === "candidate") {
          label = `${top.data.media_name}${top.data.media_year ? ` (${top.data.media_year})` : ""}`;
          conf = top.confidence;
        } else if (top?.kind === "provider") {
          label = `${top.data.name}${top.data.year ? ` (${top.data.year})` : ""}`;
          conf = top.confidence;
        } else {
          label = "Unmatched";
          conf = null;
        }
        return (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onChooseDestination(it);
            }}
            className={`mr-3 inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs hover:bg-muted ${
              staged ? "border-primary bg-primary/10" : ""
            }`}
            title="Choose destination"
          >
            <span className="max-w-[160px] truncate">{label}</span>
            {conf != null ? <MatchConfidencePill confidence={conf} /> : null}
          </button>
        );
      },
    },
    {
      id: "kind",
      header: "Type",
      width: "92px",
      render: (it) => (
        <TypePill>
          {it.kind === "scan" ? "Scan" : it.kind === "integrity" ? "Integrity" : "Torrent"}
        </TypePill>
      ),
    },
    {
      id: "progress",
      header: "Progress",
      width: "84px",
      hideBelow: "md",
      render: (it) => {
        if (isTorrent(it) || isMedia(it)) {
          const p = isTorrent(it) ? it.entry.progress : it.progress;
          return (
            <div className="flex flex-wrap items-center gap-1">
              <MetaPill className="tabular-nums">
                {p.imported}/{p.total}
              </MetaPill>
            </div>
          );
        }
        if (isIntegrity(it)) {
          return (
            <div className="flex flex-wrap items-center gap-1">
              <MetaPill className="uppercase">{qualityToString(it.mismatch.quality)}</MetaPill>
              {it.mismatch.variant_tag ? (
                <MetaPill className="font-mono">{it.mismatch.variant_tag}</MetaPill>
              ) : null}
            </div>
          );
        }
        const r = it.result;
        const videos = r.files?.filter((f) => f.is_video).length ?? 0;
        if (videos === 0) {
          return <span className="text-muted-foreground">—</span>;
        }
        const label = videos === 1 ? "video" : "videos";
        return (
          <div className="flex flex-wrap items-center gap-1">
            <MetaPill className="tabular-nums" title={`${videos} ${label} in source dir`}>
              {`${videos} ${label}`}
            </MetaPill>
          </div>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      width: "112px",
      hideBelow: "md",
      render: (it) => {
        if (isTorrent(it)) {
          const p = it.entry.progress;
          // Reflect IMPORT outcome, not just the download state — a finished
          // download with a failed import must not read "Finished".
          let pill: React.ReactNode;
          if (p.failed > 0) {
            pill = <StatusPill status="failed" title={p.last_error ?? "Import failed"} />;
          } else if (p.ambiguous > 0) {
            pill = <StatusPill status="ambiguous" />;
          } else if (p.total > 0 && p.imported >= p.total) {
            pill = <StatusPill status="imported" />;
          } else {
            pill = <StatusPill status={getTorrentStatusString(it.entry.torrent_status)} />;
          }
          return <div className="flex flex-wrap items-center gap-1">{pill}</div>;
        }
        if (isMedia(it)) {
          const p = it.progress;
          const status = p.total > 0 && p.imported >= p.total ? "imported" : "pending";
          return (
            <div className="flex flex-wrap items-center gap-1">
              <StatusPill status={status} />
            </div>
          );
        }
        if (isIntegrity(it)) {
          return (
            <div className="flex flex-wrap items-center gap-1">
              <StatusPill status="corrupt" label={CORRUPT_LABEL} title={it.mismatch.import_error} />
            </div>
          );
        }
        const r = it.result;
        const scanPill =
          r.status === "failed" ? (
            <StatusPill status="failed" title={r.import_error ?? "Import failed"} />
          ) : r.status === "imported" ? (
            <StatusPill status="imported" />
          ) : (
            <StatusPill status="pending" />
          );
        return <div className="flex flex-wrap items-center gap-1">{scanPill}</div>;
      },
    },
  ];
}

export const IMPORT_GROUPINGS: GroupByDef<ImportItem>[] = [
  {
    id: "bucket",
    label: "Status",
    getGroup: (it) => {
      const b = bucketOf(it);
      return { key: b, label: b, sortOrder: BUCKET_ORDER[b] ?? 99 };
    },
  },
  {
    id: "kind",
    label: "Type",
    getGroup: (it) => ({
      key: it.kind,
      label: KIND_LABELS[it.kind],
      sortOrder: KIND_ORDER.indexOf(it.kind),
    }),
  },
];

export const IMPORT_FACETS: FacetDef<ImportItem>[] = [
  {
    id: "bucket",
    label: "Status",
    options: [
      { value: "Review", label: "Review" },
      { value: "Retry", label: "Retry" },
      { value: "Done", label: "Done" },
    ],
    predicate: (it, values, op) => {
      const hit = values.includes(bucketOf(it));
      return op === "excludes" ? !hit : hit;
    },
  },
  {
    id: "kind",
    label: "Type",
    options: KIND_ORDER.map((kind) => ({ value: kind, label: KIND_LABELS[kind] })),
    predicate: (it, values, op) => {
      const hit = values.includes(it.kind);
      return op === "excludes" ? !hit : hit;
    },
  },
];

/** Free-text search predicate for an import row. */
export function importSearchMatch(it: ImportItem, q: string): boolean {
  if (isTorrent(it)) {
    return (
      it.entry.torrent_title.toLowerCase().includes(q) ||
      (it.entry.media?.media_name ?? "").toLowerCase().includes(q)
    );
  }
  if (isMedia(it)) {
    return it.media_name.toLowerCase().includes(q) || it.torrent_title.toLowerCase().includes(q);
  }
  if (isIntegrity(it)) {
    return (
      it.mismatch.media_title.toLowerCase().includes(q) ||
      (it.mismatch.path ?? "").toLowerCase().includes(q)
    );
  }
  return (
    it.result.detected_name.toLowerCase().includes(q) ||
    it.result.directory.toLowerCase().includes(q)
  );
}

/** Whether a row has expandable content (mirrors `ImportExpandedContent`). */
export function isImportExpandable(it: ImportItem): boolean {
  if (isTorrent(it)) return it.entry.files.length > 0;
  if (isMedia(it)) return it.files.length > 0;
  return true;
}

/** Expanded per-file / mismatch detail for one import row. */
export function ImportExpandedContent({ item: it }: { item: ImportItem }): React.ReactNode {
  if (isIntegrity(it)) {
    const m = it.mismatch;
    return (
      <div className="flex flex-wrap items-center gap-2 bg-black/30 px-3 py-2 text-xs">
        <StatusPill status="corrupt" label={CORRUPT_LABEL} className="shrink-0" />
        <span className="truncate font-mono" title={m.path ?? undefined}>
          {m.path ?? "—"}
        </span>
        {m.detected_at ? (
          <MetaPill title="Detected">{new Date(m.detected_at).toLocaleDateString()}</MetaPill>
        ) : null}
        {m.import_error ? (
          <span className="ml-auto truncate text-red-500" title={m.import_error}>
            {m.import_error}
          </span>
        ) : null}
      </div>
    );
  }
  if (isTorrent(it) || isMedia(it)) {
    const files = isTorrent(it) ? it.entry.files : it.files;
    if (files.length === 0) return null;
    return (
      <div className="bg-black/30 p-2">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-2">
          {files.map((file, i) => (
            <div
              key={`${file.media_label}-${i}`}
              className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-3 py-2 text-xs"
            >
              <StatusPill
                status={file.import_status}
                label={file.import_status.startsWith("failed") ? "Failed" : undefined}
                className="shrink-0"
              />
              <span className="shrink-0 font-mono">{file.media_label}</span>
              {file.variant && (
                <span className="shrink-0 font-mono text-muted-foreground">· {file.variant}</span>
              )}
              {file.import_error && (
                <span className="ml-auto truncate text-red-500" title={file.import_error}>
                  {file.import_error}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }
  const r = it.result;
  const sfiles = r.files ?? [];
  if (sfiles.length === 0) {
    return (
      <div className="flex items-center justify-center bg-black/30 px-4 py-8 text-center text-xs text-muted-foreground">
        No files listed — re-run the scan to refresh.
      </div>
    );
  }
  const scanFileStatus =
    r.status === "failed" ? "failed" : r.status === "imported" ? "imported" : "pending";
  return (
    <div className="bg-black/30 p-2">
      <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-2">
        {sfiles.map((f, i) => (
          <div
            key={`${f.relative_path}-${i}`}
            className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-3 py-2 text-xs"
          >
            <StatusPill status={scanFileStatus} className="h-5 shrink-0 px-1.5 text-[10px]" />
            <span className="truncate font-mono" title={f.relative_path}>
              {f.relative_path}
            </span>
            <TypePill className="ml-auto h-5 shrink-0 px-1.5 text-[10px] uppercase">
              {f.is_video ? "video" : "file"}
            </TypePill>
          </div>
        ))}
      </div>
    </div>
  );
}

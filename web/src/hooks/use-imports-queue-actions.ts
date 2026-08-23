"use client";

import * as React from "react";
import { toast } from "sonner";

import apiClient from "@/lib/api/client";
import { bulkMutate } from "@/lib/bulk-mutate";
import { isIntegrity, isMedia, isTorrent } from "@/lib/imports";
import type {
  ImportItem,
  IntegrityImport,
  ScanCandidate,
  ScanImport,
  ScanProviderCandidate,
  StagedChoice,
  TorrentImport,
} from "@/lib/imports";

export async function resolveTorrentRetry(
  item: TorrentImport,
  {
    setBusyId,
    refreshAll,
  }: {
    setBusyId: (id: string | null) => void;
    refreshAll: () => void;
  },
) {
  setBusyId(item.id);
  try {
    const { error } = await apiClient.POST("/api/v1/imports/resolve", {
      body: { kind: "torrent", id: item.id, action: "retry" },
    });
    if (error) throw new Error("retry failed");
    toast.success(`Queued retry for "${item.entry.torrent_title}"`, {
      description: "Re-import will run in the background.",
    });
    refreshAll();
  } catch {
    toast.error("Could not queue retry.");
  } finally {
    setBusyId(null);
  }
}

export async function pickScanCandidate(
  item: ScanImport,
  candidate: ScanCandidate,
  {
    setBusyId,
    markQueued,
    unmarkQueued,
    refreshAll,
  }: {
    setBusyId: (id: string | null) => void;
    markQueued: (ids: string[]) => void;
    unmarkQueued: (ids: string[]) => void;
    refreshAll: () => void;
  },
) {
  setBusyId(item.id);
  markQueued([item.id]);
  try {
    const { error } = await apiClient.POST("/api/v1/imports/resolve", {
      body: {
        kind: "scan",
        id: item.id,
        media_type: candidate.media_type,
        media_id: candidate.media_id,
      },
    });
    if (error) throw new Error("import failed");
    toast.success(`Queued "${item.result.detected_name}" → ${candidate.media_name}`, {
      description: "Import will run in the background.",
    });
    refreshAll();
  } catch {
    unmarkQueued([item.id]);
    toast.error("Could not queue import.");
  } finally {
    setBusyId(null);
  }
}

export async function pickProviderCandidate(
  item: ScanImport,
  candidate: ScanProviderCandidate,
  {
    setBusyId,
    markQueued,
    unmarkQueued,
    refreshAll,
  }: {
    setBusyId: (id: string | null) => void;
    markQueued: (ids: string[]) => void;
    unmarkQueued: (ids: string[]) => void;
    refreshAll: () => void;
  },
) {
  setBusyId(item.id);
  markQueued([item.id]);
  try {
    const { error } = await apiClient.POST("/api/v1/imports/resolve", {
      body: {
        kind: "scan",
        id: item.id,
        media_type: candidate.media_type,
        external_id: candidate.external_id,
        metadata_provider: candidate.metadata_provider,
      },
    });
    if (error) throw new Error("import failed");
    toast.success(`Queued "${item.result.detected_name}" → ${candidate.name}`, {
      description: "Library entry will be created and the import will run in the background.",
    });
    refreshAll();
  } catch {
    unmarkQueued([item.id]);
    toast.error("Could not queue import.");
  } finally {
    setBusyId(null);
  }
}

// Corrupt-file rows resolve through the dedicated integrity endpoints, not
// /imports/resolve: "rebaseline" accepts the on-disk file (checksum re-read
// next audit), "dismiss" keeps the original checksum and re-verifies.
export async function resolveIntegrity(
  item: IntegrityImport,
  action: "rebaseline" | "dismiss",
  {
    setBusyId,
    refreshAll,
  }: {
    setBusyId: (id: string | null) => void;
    refreshAll: () => void;
  },
) {
  const m = item.mismatch;
  const msg =
    action === "rebaseline"
      ? `Accept the current file for "${m.media_title}"? Its checksum will be re-baselined from disk on the next audit.`
      : `Dismiss the mismatch for "${m.media_title}"? The original checksum is kept and re-verified on the next audit.`;
  if (!confirm(msg)) return;
  setBusyId(item.id);
  try {
    const params = { path: { media_type: m.media_type, file_id: m.file_id } };
    const { error } =
      action === "rebaseline"
        ? await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/rebaseline", {
            params,
          })
        : await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/dismiss", {
            params,
          });
    if (error) throw new Error("action failed");
    toast.success(
      action === "rebaseline"
        ? `Accepted current file for "${m.media_title}".`
        : `Dismissed mismatch for "${m.media_title}".`,
    );
    refreshAll();
  } catch {
    toast.error("Action failed.");
  } finally {
    setBusyId(null);
  }
}

export async function ignoreItem(
  item: ImportItem,
  {
    setBusyId,
    refreshAll,
  }: {
    setBusyId: (id: string | null) => void;
    refreshAll: () => void;
  },
) {
  if (isMedia(item) || isIntegrity(item)) return;
  const isTor = isTorrent(item);
  const label = isTor ? item.entry.torrent_title : item.result.detected_name;
  const msg = isTor
    ? `Remove torrent "${label}" and delete files?`
    : `Ignore "${item.result.directory}" forever?`;
  if (!confirm(msg)) return;
  setBusyId(item.id);
  try {
    const { error } = await apiClient.POST("/api/v1/imports/ignore", {
      body: { kind: item.kind, id: item.id, delete_files: isTor },
    });
    if (error) throw new Error("ignore failed");
    toast.success(isTor ? "Torrent removed." : "Path ignored.");
    refreshAll();
  } catch {
    toast.error("Action failed.");
  } finally {
    setBusyId(null);
  }
}

export async function bulkRetry(
  itemsToRetry: ImportItem[],
  { refreshAll }: { refreshAll: () => void },
) {
  const torrents = itemsToRetry.filter(isTorrent);
  if (torrents.length === 0) {
    toast.info("Select torrent rows to retry.");
    return;
  }
  if (!confirm(`Retry ${torrents.length} torrent(s)?`)) return;
  const { ok, failed } = await bulkMutate(torrents, (t) =>
    apiClient.POST("/api/v1/imports/resolve", {
      body: { kind: "torrent", id: t.id, action: "retry" },
    }),
  );
  if (failed === 0) {
    toast.success(`Queued retry for ${ok}/${torrents.length}`, {
      description: "Re-imports will run in the background.",
    });
  } else if (ok === 0) {
    toast.error(`${failed} retry request(s) failed.`);
  } else {
    toast.warning(`Queued retry for ${ok}/${torrents.length}, ${failed} failed`);
  }
  refreshAll();
}

// Only scan rows that aren't already imported/queued and have a
// destination to import into. (Torrent rows are handled by Retry.)
export async function bulkImport(
  selected: ImportItem[],
  {
    queuedScanIds,
    effectiveChoiceFor,
    markQueued,
    unmarkQueued,
    setStagedByScan,
    refreshAll,
  }: {
    queuedScanIds: Set<string>;
    effectiveChoiceFor: (item: ScanImport) => StagedChoice | null;
    markQueued: (ids: string[]) => void;
    unmarkQueued: (ids: string[]) => void;
    setStagedByScan: React.Dispatch<React.SetStateAction<Record<string, StagedChoice>>>;
    refreshAll: () => void;
  },
) {
  const targets = selected
    .filter(
      (it): it is ScanImport =>
        it.kind === "scan" &&
        it.result.status !== "imported" &&
        it.result.status !== "queued" &&
        !queuedScanIds.has(it.id),
    )
    .map((it) => ({ it, choice: effectiveChoiceFor(it) }))
    .filter((x): x is { it: ScanImport; choice: StagedChoice } => x.choice !== null);

  if (targets.length === 0) {
    toast.info("Select scan rows with a destination to import.");
    return;
  }

  const ids = targets.map((t) => t.it.id);
  markQueued(ids);
  setStagedByScan((prev) => {
    const next = { ...prev };
    for (const id of ids) delete next[id];
    return next;
  });

  const { failedItems } = await bulkMutate(targets, ({ it, choice }) =>
    apiClient.POST("/api/v1/imports/resolve", {
      body:
        choice.kind === "candidate"
          ? {
              kind: "scan",
              id: it.id,
              media_type: choice.data.media_type,
              media_id: choice.data.media_id,
            }
          : {
              kind: "scan",
              id: it.id,
              media_type: choice.data.media_type,
              external_id: choice.data.external_id,
              metadata_provider: choice.data.metadata_provider,
            },
    }),
  );

  const failedIds = failedItems.map((t) => t.it.id);
  if (failedIds.length > 0) unmarkQueued(failedIds);
  const ok = ids.length - failedIds.length;
  if (ok > 0) {
    toast.success(`Queued ${ok} import${ok === 1 ? "" : "s"}`, {
      description: "Imports will run in the background.",
    });
  }
  if (failedIds.length > 0) {
    toast.error(`${failedIds.length} import(s) could not be queued.`);
  }
  refreshAll();
}

export function useImportsQueueActions({
  refreshAll,
  markQueued,
  unmarkQueued,
  queuedScanIds,
  setStagedByScan,
  effectiveChoiceFor,
}: {
  refreshAll: () => void;
  markQueued: (ids: string[]) => void;
  unmarkQueued: (ids: string[]) => void;
  queuedScanIds: Set<string>;
  setStagedByScan: React.Dispatch<React.SetStateAction<Record<string, StagedChoice>>>;
  effectiveChoiceFor: (item: ScanImport) => StagedChoice | null;
}) {
  const [busyId, setBusyId] = React.useState<string | null>(null);

  const resolveTorrentRetryCb = React.useCallback(
    async (item: TorrentImport) => resolveTorrentRetry(item, { setBusyId, refreshAll }),
    [refreshAll],
  );

  const pickScanCandidateCb = React.useCallback(
    async (item: ScanImport, candidate: ScanCandidate) =>
      pickScanCandidate(item, candidate, { setBusyId, markQueued, unmarkQueued, refreshAll }),
    [markQueued, unmarkQueued, refreshAll],
  );

  const pickProviderCandidateCb = React.useCallback(
    async (item: ScanImport, candidate: ScanProviderCandidate) =>
      pickProviderCandidate(item, candidate, { setBusyId, markQueued, unmarkQueued, refreshAll }),
    [markQueued, unmarkQueued, refreshAll],
  );

  const resolveIntegrityCb = React.useCallback(
    async (item: IntegrityImport, action: "rebaseline" | "dismiss") =>
      resolveIntegrity(item, action, { setBusyId, refreshAll }),
    [refreshAll],
  );

  const ignoreItemCb = React.useCallback(
    async (item: ImportItem) => ignoreItem(item, { setBusyId, refreshAll }),
    [refreshAll],
  );

  const bulkRetryCb = React.useCallback(
    async (itemsToRetry: ImportItem[]) => bulkRetry(itemsToRetry, { refreshAll }),
    [refreshAll],
  );

  const bulkImportCb = React.useCallback(
    async (selected: ImportItem[]) =>
      bulkImport(selected, {
        queuedScanIds,
        effectiveChoiceFor,
        markQueued,
        unmarkQueued,
        setStagedByScan,
        refreshAll,
      }),
    [queuedScanIds, effectiveChoiceFor, markQueued, unmarkQueued, setStagedByScan, refreshAll],
  );

  return {
    busyId,
    resolveTorrentRetry: resolveTorrentRetryCb,
    pickScanCandidate: pickScanCandidateCb,
    pickProviderCandidate: pickProviderCandidateCb,
    resolveIntegrity: resolveIntegrityCb,
    ignoreItem: ignoreItemCb,
    bulkRetry: bulkRetryCb,
    bulkImport: bulkImportCb,
  };
}

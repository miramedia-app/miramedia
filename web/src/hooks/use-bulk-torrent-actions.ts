"use client";

import * as React from "react";
import { toast } from "sonner";

import apiClient from "@/lib/api/client";
import { bulkMutate, type BulkMutateResult } from "@/lib/bulk-mutate";

type BulkTorrentVerb = "paused" | "resumed" | "deleted" | "retried";

const actions: Record<BulkTorrentVerb, string> = {
  paused: "pause",
  resumed: "resume",
  deleted: "delete",
  retried: "retry",
};

function failureMessage(verb: BulkTorrentVerb, failurePeriod: boolean) {
  return `Failed to ${actions[verb]} some torrents${failurePeriod ? "." : ""}`;
}

interface ReportBulkResultOptions {
  successPeriod?: boolean;
  failurePeriod?: boolean;
}

export function reportBulkResult(
  ok: number,
  failed: number,
  verb: BulkTorrentVerb,
  { successPeriod = false, failurePeriod = false }: ReportBulkResultOptions = {},
) {
  if (failed === 0) {
    toast.success(`${ok} torrent${ok !== 1 ? "s" : ""} ${verb}${successPeriod ? "." : ""}`);
  } else if (ok === 0) {
    toast.error(failureMessage(verb, failurePeriod));
  } else {
    toast.warning(`${ok} ${verb}, ${failed} failed`);
  }
}

interface UseBulkTorrentActionsOptions {
  deleteSuccessPeriod?: boolean;
  failurePeriod?: boolean;
}

interface RemoveOptions {
  blockHash?: boolean;
  onResult?: (result: BulkMutateResult<string>) => void;
}

type TorrentCall = (id: string) => Promise<{ error?: unknown }>;

export function useBulkTorrentActions(
  invalidateAll: () => Promise<void>,
  { deleteSuccessPeriod = false, failurePeriod = false }: UseBulkTorrentActionsOptions = {},
) {
  const [bulkWorking, setBulkWorking] = React.useState(false);

  const run = React.useCallback(
    async (
      ids: string[],
      call: TorrentCall,
      verb: BulkTorrentVerb,
      onResult?: (result: BulkMutateResult<string>) => void,
    ) => {
      if (!ids.length) return null;

      setBulkWorking(true);
      try {
        const result = await bulkMutate(ids, call);
        reportBulkResult(result.ok, result.failed, verb, {
          successPeriod: verb === "deleted" && deleteSuccessPeriod,
          failurePeriod,
        });
        onResult?.(result);
        await invalidateAll();
        return result;
      } catch {
        toast.error(failureMessage(verb, failurePeriod));
        return null;
      } finally {
        setBulkWorking(false);
      }
    },
    [deleteSuccessPeriod, failurePeriod, invalidateAll],
  );

  const pause = React.useCallback(
    (ids: string[]) =>
      run(
        ids,
        (id) =>
          apiClient.POST("/api/v1/torrents/{torrent_id}/pause", {
            params: { path: { torrent_id: id } },
          }),
        "paused",
      ),
    [run],
  );

  const resume = React.useCallback(
    (ids: string[]) =>
      run(
        ids,
        (id) =>
          apiClient.POST("/api/v1/torrents/{torrent_id}/resume", {
            params: { path: { torrent_id: id } },
          }),
        "resumed",
      ),
    [run],
  );

  const retry = React.useCallback(
    (ids: string[]) =>
      run(
        ids,
        (id) =>
          apiClient.POST("/api/v1/torrents/{torrent_id}/retry", {
            params: { path: { torrent_id: id } },
          }),
        "retried",
      ),
    [run],
  );

  const remove = React.useCallback(
    (ids: string[], { blockHash, onResult }: RemoveOptions = {}) =>
      run(
        ids,
        (id) =>
          blockHash === undefined
            ? apiClient.DELETE("/api/v1/torrents/{torrent_id}", {
                params: { path: { torrent_id: id } },
              })
            : apiClient.DELETE("/api/v1/torrents/{torrent_id}", {
                params: {
                  path: { torrent_id: id },
                  query: { block_hash: blockHash },
                },
              }),
        "deleted",
        onResult,
      ),
    [run],
  );

  // Single-torrent wrappers: pages used to hand-roll these with their own
  // toasts and invalidation, which drifted apart. Everything now shares the
  // hook's mechanical wording ("1 torrent retried").
  const pauseOne = React.useCallback((id: string) => pause([id]), [pause]);
  const resumeOne = React.useCallback((id: string) => resume([id]), [resume]);
  const retryOne = React.useCallback((id: string) => retry([id]), [retry]);

  return { bulkWorking, pause, resume, retry, remove, pauseOne, resumeOne, retryOne };
}

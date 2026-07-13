"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, LoaderCircle, ShieldCheck, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MetaPill } from "@/components/ui/type-pill";
import { qk } from "@/lib/query-keys";
import { qualityToString } from "@/lib/utils";
import apiClient from "@/lib/api/client";
import type { components } from "@/lib/api/api";

type IntegrityMismatch = components["schemas"]["IntegrityMismatch"];

const MISMATCHES_KEY = ["imports", "integrity", "mismatches"] as const;

function rowKey(m: IntegrityMismatch): string {
  return `${m.media_type}:${m.file_id}`;
}

function formatDetectedAt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

/**
 * Corrupted-files view for the imports dashboard. Lists imported files whose
 * SHA1 integrity audit recorded a mismatch (bit-rot), with two operator
 * actions per row: accept the current on-disk bytes as the new baseline, or
 * dismiss (keep the original checksum and re-verify next audit).
 */
export function CorruptedFilesPanel() {
  const qc = useQueryClient();
  const [busyKey, setBusyKey] = React.useState<string | null>(null);
  // Optimistically-removed rows. The action endpoints clear the mismatch
  // stamp, but the list refetch lags the fast POST — hide the row immediately
  // and let the refetch reconcile.
  const [removed, setRemoved] = React.useState<Set<string>>(() => new Set());

  const query = useQuery({
    queryKey: [...MISMATCHES_KEY],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/torrents/integrity/mismatches");
      if (error) throw error;
      return data ?? [];
    },
  });

  const all = query.data ?? [];
  const rows = all.filter((m) => !removed.has(rowKey(m)));

  // Prune the optimistic-removed set once the server no longer returns those
  // rows, so a genuinely re-flagged file can reappear later.
  React.useEffect(() => {
    setRemoved((prev) => {
      if (prev.size === 0) return prev;
      const present = new Set(all.map(rowKey));
      const next = new Set<string>();
      for (const k of prev) if (present.has(k)) next.add(k);
      return next.size === prev.size ? prev : next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data]);

  async function runAction(m: IntegrityMismatch, action: "rebaseline" | "dismiss") {
    const key = rowKey(m);
    const msg =
      action === "rebaseline"
        ? `Accept the current file for "${m.media_title}"? Its checksum will be re-baselined from disk on the next audit.`
        : `Dismiss the mismatch for "${m.media_title}"? The original checksum is kept and re-verified on the next audit.`;
    if (!confirm(msg)) return;
    setBusyKey(key);
    try {
      const path = { media_type: m.media_type, file_id: m.file_id };
      const { error } =
        action === "rebaseline"
          ? await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/rebaseline", {
              params: { path },
            })
          : await apiClient.POST("/api/v1/torrents/integrity/{media_type}/{file_id}/dismiss", {
              params: { path },
            });
      if (error) throw new Error("action failed");
      toast.success(
        action === "rebaseline"
          ? `Accepted current file for "${m.media_title}".`
          : `Dismissed mismatch for "${m.media_title}".`,
      );
      setRemoved((prev) => new Set(prev).add(key));
      void qc.invalidateQueries({ queryKey: qk.imports.counts() });
      void query.refetch();
    } catch {
      toast.error("Action failed.");
    } finally {
      setBusyKey(null);
    }
  }

  if (query.isError) {
    return (
      <div className="rounded-lg border border-border/60 bg-card px-4 py-6 text-center text-sm text-muted-foreground">
        Could not load integrity mismatches.
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-border/60 bg-card px-4 py-10 text-center text-sm text-muted-foreground">
        <ShieldCheck className="h-6 w-6 opacity-60" />
        No integrity issues detected.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {rows.map((m) => {
        const key = rowKey(m);
        const busy = busyKey === key;
        return (
          <div
            key={key}
            className="flex flex-col gap-2 rounded-lg border border-border/60 bg-card px-3 py-2.5 md:flex-row md:items-center"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium">{m.media_title}</span>
                {m.episode ? <MetaPill className="shrink-0 font-mono">{m.episode}</MetaPill> : null}
                <MetaPill className="shrink-0 uppercase">{qualityToString(m.quality)}</MetaPill>
                {m.variant_tag ? (
                  <MetaPill className="shrink-0 font-mono">{m.variant_tag}</MetaPill>
                ) : null}
              </div>
              {m.path ? (
                <span className="truncate font-mono text-xs text-muted-foreground" title={m.path}>
                  {m.path}
                </span>
              ) : null}
              <span className="truncate text-xs text-muted-foreground" title={m.import_error}>
                {m.import_error}
              </span>
              <span className="text-xs text-muted-foreground">
                Detected {formatDetectedAt(m.detected_at)}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void runAction(m, "rebaseline")}
                title="Re-baseline the checksum from the file on disk next audit"
              >
                {busy ? (
                  <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="mr-1 h-3.5 w-3.5" />
                )}
                Accept current
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-muted-foreground"
                disabled={busy}
                onClick={() => void runAction(m, "dismiss")}
                title="Keep the original checksum; re-verify next audit"
              >
                <X className="mr-1 h-3.5 w-3.5" />
                Dismiss
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

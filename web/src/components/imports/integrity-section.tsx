"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertOctagon, Check, ChevronLeft, ChevronRight, LoaderCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { MetaPill } from "@/components/ui/type-pill";
import { useUser } from "@/components/providers/user-provider";
import apiClient from "@/lib/api/client";
import { qk } from "@/lib/query-keys";
import { qualityToString } from "@/lib/utils";
import {
  CORRUPT_LABEL,
  INTEGRITY_PAGE_SIZE,
  MISMATCH_MEDIA_LABELS,
  MISMATCH_MEDIA_ORDER,
  canActOnMismatch,
  canQueryIntegrity,
  mismatchPageCounts,
  mismatchRowId,
  pageRangeLabel,
  previousOffset,
  shouldStepBack,
  visibleMismatches,
} from "@/lib/imports";
import type { IntegrityMismatch } from "@/lib/imports";

/**
 * File-integrity (bit-rot) mismatches.
 *
 * Deliberately its own section rather than rows in the imports DataList: the
 * endpoint is superuser-only and server-paginated, so exactly one page is on
 * screen at a time and nothing here claims to sort or filter across pages. Its
 * loading/error state is independent — a failing audit fetch must never blank
 * the scan/torrent rows that loaded fine.
 */
export function IntegritySection() {
  const qc = useQueryClient();
  const { user, isLoading: userLoading } = useUser();
  const canSee = canQueryIntegrity({ userLoading, isSuperuser: user?.is_superuser });

  const [offset, setOffset] = React.useState(0);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  // Optimistically-removed rows: the action endpoints clear the mismatch stamp
  // but the refetch lags the fast POST — hide the row now, reconcile on refetch.
  const [removed, setRemoved] = React.useState<ReadonlySet<string>>(() => new Set());

  const query = useQuery({
    queryKey: qk.imports.integrity(offset, INTEGRITY_PAGE_SIZE),
    // Signal-aware: on role loss we `cancelQueries` this key and the privileged
    // request is aborted in transport rather than left to land.
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/torrents/integrity/mismatches", {
        params: { query: { offset, limit: INTEGRITY_PAGE_SIZE } },
        signal,
      });
      if (error) throw error;
      return data!;
    },
    enabled: canSee,
    // Hold the previous page while the next one loads (no layout jump), but only
    // while authorized — a demoted session must never keep painting admin rows.
    placeholderData: (prev) => (canSee ? prev : undefined),
  });

  // Authorization dropped (logout, demotion, or a slow `users/me` resolving to a
  // non-superuser): abort in-flight privileged requests, evict every cached page,
  // and reset paging + optimistic state so nothing survives for the next account.
  React.useEffect(() => {
    if (canSee) return;
    void qc.cancelQueries({ queryKey: qk.imports.integrity() });
    qc.removeQueries({ queryKey: qk.imports.integrity() });
    setOffset(0);
    setBusyId(null);
    setRemoved((prev) => (prev.size === 0 ? prev : new Set()));
  }, [canSee, qc]);

  const page = query.data;
  const rows = React.useMemo(
    () => visibleMismatches({ page, canSee, removed }),
    [page, canSee, removed],
  );

  // Prune the optimistic-removed set once the server stops returning those rows,
  // so a genuinely re-flagged file can reappear on a later audit.
  React.useEffect(() => {
    if (!page) return;
    setRemoved((prev) => {
      if (prev.size === 0) return prev;
      const present = new Set(page.items.map(mismatchRowId));
      const next = new Set<string>();
      for (const k of prev) if (present.has(k)) next.add(k);
      return next.size === prev.size ? prev : next;
    });
  }, [page]);

  // Out-of-range or emptied page (last row resolved, or rows dropped between
  // fetches): fall back one page. `shouldStepBack` is false at offset 0, so this
  // walks home and stops — it cannot loop.
  React.useEffect(() => {
    if (!canSee || query.isPending || query.isFetching || query.isError) return;
    if (shouldStepBack({ offset, count: rows.length })) {
      setOffset((o) => previousOffset(o, INTEGRITY_PAGE_SIZE));
    }
  }, [canSee, query.isPending, query.isFetching, query.isError, offset, rows.length]);

  async function resolve(m: IntegrityMismatch, action: "rebaseline" | "dismiss") {
    const rowId = mismatchRowId(m);
    // Defence in depth: controls are hidden for non-superusers and while an
    // action is in flight, but never let a stale render fire a privileged POST
    // (or double-submit) anyway.
    if (!canActOnMismatch({ canSee, busy: busyId !== null })) return;
    const msg =
      action === "rebaseline"
        ? `Accept the current file for "${m.media_title}"? Its checksum will be re-baselined from disk on the next audit.`
        : `Dismiss the mismatch for "${m.media_title}"? The original checksum is kept and re-verified on the next audit.`;
    if (!confirm(msg)) return;
    setBusyId(rowId);
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
      setRemoved((prev) => new Set(prev).add(rowId));
      // Refresh the page we are on plus the import views whose counts include it.
      // If that leaves this (non-zero) page empty, the step-back effect lands us
      // on the previous one.
      void qc.invalidateQueries({ queryKey: qk.imports.integrity(offset, INTEGRITY_PAGE_SIZE) });
      void qc.invalidateQueries({ queryKey: qk.imports.list() });
      void qc.invalidateQueries({ queryKey: qk.imports.counts() });
    } catch {
      // The page stays exactly as it was; the error is not sticky-fatal.
      toast.error("Action failed.");
    } finally {
      setBusyId(null);
    }
  }

  // Ordinary users issue no privileged request and render no privileged markup.
  if (!canSee) return null;

  const total = page?.total ?? 0;
  const counts = mismatchPageCounts(rows);
  const busy = busyId !== null;
  const paging = query.isFetching || busy;
  const canPrev = offset > 0 && !paging;
  const canNext = page?.next_offset != null && !paging;

  return (
    <section
      aria-labelledby="integrity-heading"
      className="rounded-lg border border-border/60 bg-card"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/60 px-4 py-3">
        <AlertOctagon className="h-4 w-4 shrink-0 text-destructive" />
        <h2 id="integrity-heading" className="text-sm font-medium">
          File integrity issues
        </h2>
        <StatusPill status="corrupt" label={CORRUPT_LABEL} />
        {query.isFetching ? (
          <LoaderCircle
            aria-hidden
            className="h-3.5 w-3.5 animate-spin text-muted-foreground"
            data-testid="integrity-fetching"
          />
        ) : null}
        <span className="sr-only" aria-live="polite">
          {query.isFetching
            ? "Loading file integrity issues"
            : `Showing ${pageRangeLabel({ offset, count: rows.length, total })} file integrity issues`}
        </span>
        <p className="ml-auto text-xs text-muted-foreground tabular-nums">
          {pageRangeLabel({ offset, count: rows.length, total })}
        </p>
      </div>

      {query.isError ? (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 px-4 py-6 text-sm"
        >
          <span>Corruption data could not be loaded. Other imports are unaffected.</span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            {query.isFetching ? <LoaderCircle className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
            Retry
          </Button>
        </div>
      ) : query.isPending ? (
        <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
          <LoaderCircle className="h-4 w-4 animate-spin" />
          Checking file integrity…
        </div>
      ) : rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">
          No corrupted files detected by the integrity audit.
        </p>
      ) : (
        <ul className="divide-y divide-border/60">
          {rows.map((m) => {
            const rowId = mismatchRowId(m);
            const rowBusy = busyId === rowId;
            const name = m.path?.split("/").filter(Boolean).pop() ?? "—";
            return (
              <li
                key={rowId}
                className="flex flex-col gap-2 px-4 py-3 md:flex-row md:items-center md:gap-4"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">
                    {m.media_title}
                    {m.episode ? (
                      <span className="ml-1.5 font-mono text-xs text-muted-foreground">
                        {m.episode}
                      </span>
                    ) : null}
                  </p>
                  <p
                    className="truncate font-mono text-xs text-muted-foreground"
                    title={m.path ?? undefined}
                  >
                    {name}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1">
                  <MetaPill className="uppercase">{qualityToString(m.quality)}</MetaPill>
                  {m.variant_tag ? <MetaPill className="font-mono">{m.variant_tag}</MetaPill> : null}
                  <MetaPill title={m.import_error}>
                    {m.detected_at ? new Date(m.detected_at).toLocaleDateString() : "—"}
                  </MetaPill>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void resolve(m, "rebaseline")}
                    title="Re-baseline the checksum from the file on disk next audit"
                  >
                    {rowBusy ? (
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
                    onClick={() => void resolve(m, "dismiss")}
                    title="Keep the original checksum; re-verify next audit"
                  >
                    <X className="mr-1 h-3.5 w-3.5" />
                    Dismiss
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border/60 px-4 py-2">
        <p className="text-xs text-muted-foreground">
          {/* Page-scoped: this describes the rows on screen, not the whole audit. */}
          On this page:{" "}
          {MISMATCH_MEDIA_ORDER.map((t) => `${counts[t]} ${MISMATCH_MEDIA_LABELS[t]}`).join(" · ")}
        </p>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="outline"
            disabled={!canPrev}
            onClick={() => setOffset((o) => previousOffset(o, INTEGRITY_PAGE_SIZE))}
          >
            <ChevronLeft className="mr-1 h-3.5 w-3.5" />
            Previous page
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!canNext}
            onClick={() => {
              const next = page?.next_offset;
              if (next != null) setOffset(next);
            }}
          >
            Next page
            <ChevronRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </section>
  );
}

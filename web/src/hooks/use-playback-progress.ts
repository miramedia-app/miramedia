"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { invalidateWatchedCaches } from "@/hooks/use-watched-state";
import type { components } from "@/lib/api/api";

type MediaKind = components["schemas"]["MediaKind"];
type PlaybackProgress = components["schemas"]["PlaybackProgress"];

// Server coalesces writes <5s apart with <2s position delta
// (miramedia/playback/service.py) and ignores positions <5s
// (completion.py). 10s cadence stays deliberately above both.
export const REPORT_INTERVAL_MS = 10_000;

type ProgressSample = { positionMs: number; durationMs: number };

type ProgressPutBody = {
  file_id: string;
  media_kind: MediaKind;
  position_ms: number;
  duration_ms: number;
};

export type ProgressReporter = {
  report: (positionMs: number, durationMs: number) => void;
  flush: (reason?: string) => void;
  dispose: () => void;
  /** DELETE saved progress. Returns false when the request fails. */
  clearProgress: () => Promise<boolean>;
};

function sameSample(a: ProgressSample | null, b: ProgressSample | null): boolean {
  if (!a || !b) return false;
  return a.positionMs === b.positionMs && a.durationMs === b.durationMs;
}

function normalizeSample(positionMs: number, durationMs: number): ProgressSample | null {
  if (!Number.isFinite(durationMs)) return null;
  // Callers derive ms from HTMLMediaElement seconds (`currentTime * 1000`), which is
  // fractional. The API takes integer ms and rejects floats outright, so round here.
  const roundedDuration = Math.round(durationMs);
  if (roundedDuration < 1_000) return null;
  if (!Number.isFinite(positionMs)) return null;
  const clampedPosition = Math.round(Math.min(Math.max(0, positionMs), roundedDuration));
  return { positionMs: clampedPosition, durationMs: roundedDuration };
}

function isTerminalSample(sample: ProgressSample, reason?: string): boolean {
  return reason === "ended" || sample.positionMs >= sample.durationMs;
}

export function createProgressReporter({
  put,
  clear,
  fileId,
  mediaKind,
  intervalMs = REPORT_INTERVAL_MS,
  onTerminalPut,
}: {
  put: (body: ProgressPutBody) => void | Promise<unknown>;
  clear: () => Promise<{ error?: unknown }>;
  fileId: string;
  mediaKind: MediaKind;
  intervalMs?: number;
  onTerminalPut?: () => void;
}): ProgressReporter {
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let windowActive = false;
  let pending: ProgressSample | null = null;
  let lastSent: ProgressSample | null = null;
  let inFlight = false;
  let queued: ProgressSample | null = null;
  let queuedTerminal = false;

  function clearTimer() {
    if (timer != null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function resetSentState() {
    pending = null;
    lastSent = null;
    queued = null;
    queuedTerminal = false;
    clearTimer();
    windowActive = false;
  }

  function enqueuePut(sample: ProgressSample, terminal: boolean) {
    if (inFlight) {
      // Coalesce only unsent samples; never replace a queued terminal.
      if (queuedTerminal && !terminal) return;
      queued = sample;
      queuedTerminal = terminal || queuedTerminal;
      return;
    }

    inFlight = true;
    lastSent = sample;
    void (async () => {
      let current: ProgressSample | null = sample;
      let currentTerminal = terminal;
      while (current != null) {
        try {
          await Promise.resolve(
            put({
              file_id: fileId,
              media_kind: mediaKind,
              position_ms: current.positionMs,
              duration_ms: current.durationMs,
            }),
          );
          if (currentTerminal) onTerminalPut?.();
        } catch (err: unknown) {
          console.warn("playback progress upsert failed", err);
          // Failed send: forget it was "sent" so an identical later flush resends,
          // and restore it as pending (without clobbering a newer sample) so a
          // final flush/unmount retries even if playback stopped reporting.
          if (sameSample(lastSent, current)) lastSent = null;
          if (pending == null) pending = current;
        }
        if (queued != null) {
          current = queued;
          currentTerminal = queuedTerminal;
          queued = null;
          queuedTerminal = false;
          lastSent = current;
          continue;
        }
        current = null;
      }
      inFlight = false;
    })();
  }

  function onWindowEnd() {
    timer = null;
    if (disposed) {
      windowActive = false;
      return;
    }
    if (pending != null) {
      enqueuePut(pending, false);
      pending = null;
      timer = setTimeout(onWindowEnd, intervalMs);
      return;
    }
    windowActive = false;
  }

  function report(positionMs: number, durationMs: number) {
    if (disposed) return;
    const sample = normalizeSample(positionMs, durationMs);
    if (!sample) return;
    pending = sample;
    if (!windowActive) {
      enqueuePut(sample, false);
      pending = null;
      windowActive = true;
      timer = setTimeout(onWindowEnd, intervalMs);
    }
  }

  function flush(reason?: string) {
    if (disposed) return;
    clearTimer();
    windowActive = false;
    if (pending == null) return;
    if (sameSample(pending, lastSent) && !inFlight) {
      pending = null;
      return;
    }
    // If the same sample is already in flight, skip a duplicate enqueue unless
    // this flush is terminal and the in-flight send is not yet known terminal.
    if (sameSample(pending, lastSent) && inFlight && !isTerminalSample(pending, reason)) {
      pending = null;
      return;
    }
    enqueuePut(pending, isTerminalSample(pending, reason));
    pending = null;
  }

  async function clearProgress(): Promise<boolean> {
    if (disposed) return false;
    try {
      const { error } = await clear();
      if (error) {
        console.warn("playback progress delete failed", error);
        return false;
      }
      resetSentState();
      return true;
    } catch (err: unknown) {
      console.warn("playback progress delete failed", err);
      return false;
    }
  }

  function dispose() {
    disposed = true;
    clearTimer();
    windowActive = false;
  }

  return { report, flush, dispose, clearProgress };
}

export function usePlaybackProgress({
  fileId,
  mediaKind,
  enabled,
}: {
  fileId: string;
  mediaKind: MediaKind;
  enabled: boolean;
}): {
  initialProgress: PlaybackProgress | null | undefined;
  reporter: ProgressReporter;
} {
  const queryClient = useQueryClient();

  const progressQuery = useQuery({
    queryKey: ["playback", "progress", fileId],
    queryFn: async ({ signal }) => {
      const { data, error } = await apiClient.GET("/api/v1/playback/progress", {
        params: { query: { file_id: fileId, media_kind: mediaKind } },
        signal,
      });
      if (error) throw error;
      return data ?? null;
    },
    enabled,
    staleTime: 0,
  });

  const put = React.useCallback(async (body: ProgressPutBody) => {
    const { error } = await apiClient.PUT("/api/v1/playback/progress", { body });
    if (error) throw error;
  }, []);

  const clear = React.useCallback(async () => {
    return apiClient.DELETE("/api/v1/playback/progress", {
      params: { query: { file_id: fileId } },
    });
  }, [fileId]);

  // Create the reporter in an effect so React Strict Mode remounts get a fresh
  // instance. useMemo would reuse the disposed reporter after the first cleanup.
  const reporterRef = React.useRef<ProgressReporter | null>(null);

  React.useEffect(() => {
    const onTerminalPut = () => {
      void invalidateWatchedCaches(queryClient);
    };
    const reporter = createProgressReporter({ put, clear, fileId, mediaKind, onTerminalPut });
    reporterRef.current = reporter;
    return () => {
      reporter.flush("unmount");
      void queryClient.invalidateQueries({ queryKey: ["playback", "continue"] });
      reporter.dispose();
      if (reporterRef.current === reporter) reporterRef.current = null;
    };
  }, [put, clear, fileId, mediaKind, queryClient]);

  const wrappedReporter = React.useMemo<ProgressReporter>(
    () => ({
      report: (positionMs, durationMs) => {
        reporterRef.current?.report(positionMs, durationMs);
      },
      flush: (reason?: string) => {
        reporterRef.current?.flush(reason);
        void queryClient.invalidateQueries({ queryKey: ["playback", "continue"] });
      },
      dispose: () => {
        reporterRef.current?.dispose();
      },
      clearProgress: async () => {
        const active = reporterRef.current;
        if (!active) return false;
        const ok = await active.clearProgress();
        if (ok) {
          void queryClient.invalidateQueries({ queryKey: ["playback", "progress", fileId] });
          void queryClient.invalidateQueries({ queryKey: ["playback", "continue"] });
        }
        return ok;
      },
    }),
    [queryClient, fileId],
  );

  return {
    initialProgress: progressQuery.data,
    reporter: wrappedReporter,
  };
}

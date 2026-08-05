"use client";

import * as React from "react";
import { toast } from "sonner";

import { createManagedEventSource, type ManagedEventSource } from "@/lib/managed-event-source";
import type { Site } from "@/lib/indexers";

/**
 * Streamed indexer-site test lifecycle. Testing a site opens an SSE stream that
 * updates a single toast in place through each live phase, then resolves to the
 * real pass/fail. Re-testing or unmount aborts the prior stream cleanly.
 *
 * `onSettled(siteId)` runs whenever a test terminates by completion, closed
 * connection, or timeout (but NOT on abort) — the page uses it to invalidate
 * the sites query. Abort cleanup, the 15s stalled cap, and every toast/outcome
 * branch are preserved exactly from the original page.
 */
export function useIndexerSiteTest(onSettled: (siteId: string) => void) {
  const [testingId, setTestingId] = React.useState<string | null>(null);
  // Active test stream so re-testing / unmount aborts the prior one cleanly.
  const testStreamRef = React.useRef<ManagedEventSource | null>(null);
  React.useEffect(() => () => testStreamRef.current?.close(), []);

  const onSettledRef = React.useRef(onSettled);
  onSettledRef.current = onSettled;

  // Stream a site test over SSE. The spinner stays on the row and a single
  // toast updates in place with each live phase ("Loading page…", "Solving
  // Turnstile (attempt 3)…"), then resolves to the real pass/fail — instead of
  // a blind multi-minute wait on one blocking request.
  const testSite = React.useCallback((site: Site) => {
    testStreamRef.current?.close();
    setTestingId(site.id);
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
    const url = new URL(
      `${apiBase}/api/v1/indexers/sites/${site.id}/test/stream`,
      window.location.origin,
    );
    const toastId = toast.loading(`Testing ${site.name}…`);

    let settled = false;
    const handle = createManagedEventSource(url.toString(), {
      withCredentials: true,
      // Cap on the stalled-error state, matching the prior 15s timer. The
      // readyState CLOSED (terminal) vs CONNECTING (transient) discrimination
      // now lives in the primitive.
      timeoutMs: 15000,
      doneEvent: "done",
      events: {
        status: (ev) => {
          try {
            const { message } = JSON.parse(ev.data) as { message?: string };
            if (message) toast.loading(message, { id: toastId });
          } catch (err) {
            console.error("SSE status parse error", err);
          }
        },
        result: (ev) => {
          settled = true;
          try {
            const r = JSON.parse(ev.data) as { success?: boolean; message?: string };
            if (r.success) toast.success(r.message ?? "OK", { id: toastId });
            else toast.error(r.message ?? "Test failed", { id: toastId });
          } catch {
            toast.error("Test failed", { id: toastId });
          }
        },
      },
      onDone: (outcome) => {
        // Same terminal toasts as before, keyed off the outcome the primitive
        // reports: CLOSED → "connection lost", timer cap → "timed out". A
        // normal "done" event ("completed") leaves the result toast untouched.
        if (!settled) {
          if (outcome === "closed") {
            toast.error("Test failed — connection lost", { id: toastId });
          } else if (outcome === "timeout") {
            toast.error("Test timed out", { id: toastId });
          }
        }
        if (testStreamRef.current === handle) testStreamRef.current = null;
        setTestingId((cur) => (cur === site.id ? null : cur));
        onSettledRef.current(site.id);
      },
      // Aborted by a newer test or by unmount: `onDone` never fires, so drop
      // the loading toast and clear the row spinner here or they linger for
      // the rest of the session. No sites invalidation — the test was
      // abandoned, so it produced no site state worth refetching, and on
      // unmount there is nothing left to render.
      onAbort: () => {
        toast.dismiss(toastId);
        if (testStreamRef.current === handle) testStreamRef.current = null;
        setTestingId((cur) => (cur === site.id ? null : cur));
      },
    });
    testStreamRef.current = handle;
  }, []);

  return { testingId, testSite };
}

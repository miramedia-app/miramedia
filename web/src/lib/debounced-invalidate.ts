"use client";

import type { QueryClient, QueryKey } from "@tanstack/react-query";

const timers = new Map<string, ReturnType<typeof setTimeout>>();

/** Coalesce rapid SSE invalidations into one refetch per query key. */
export function debouncedInvalidate(qc: QueryClient, queryKey: QueryKey, delayMs = 400) {
  const key = JSON.stringify(queryKey);
  const existing = timers.get(key);
  if (existing) clearTimeout(existing);
  timers.set(
    key,
    setTimeout(() => {
      timers.delete(key);
      void qc.invalidateQueries({ queryKey });
    }, delayMs),
  );
}

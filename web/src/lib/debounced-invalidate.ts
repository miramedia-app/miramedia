"use client";

import type { QueryClient, QueryKey } from "@tanstack/react-query";

const timers = new WeakMap<QueryClient, Map<string, ReturnType<typeof setTimeout>>>();

function timersFor(qc: QueryClient): Map<string, ReturnType<typeof setTimeout>> {
  let map = timers.get(qc);
  if (!map) {
    map = new Map();
    timers.set(qc, map);
  }
  return map;
}

/** Coalesce rapid SSE invalidations into one refetch per query key. */
export function debouncedInvalidate(qc: QueryClient, queryKey: QueryKey, delayMs = 400) {
  const key = JSON.stringify(queryKey);
  const map = timersFor(qc);
  const existing = map.get(key);
  if (existing) clearTimeout(existing);
  map.set(
    key,
    setTimeout(() => {
      map.delete(key);
      void qc.invalidateQueries({ queryKey });
    }, delayMs),
  );
}

/** Drop pending debounce timers for a QueryClient. Omit queryKey to cancel all. */
export function cancelDebouncedInvalidate(qc: QueryClient, queryKey?: QueryKey) {
  const map = timers.get(qc);
  if (!map) return;
  if (queryKey !== undefined) {
    const key = JSON.stringify(queryKey);
    const existing = map.get(key);
    if (existing) {
      clearTimeout(existing);
      map.delete(key);
    }
    return;
  }
  for (const existing of map.values()) clearTimeout(existing);
  map.clear();
}

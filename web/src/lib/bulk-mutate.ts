import { pLimit } from "@/lib/p-limit";

/**
 * Runs a bulk mutation with bounded concurrency and counts HTTP failures —
 * openapi-fetch resolves with `{ error }` instead of throwing on 4xx/5xx, so a
 * plain `Promise.all` would report success for a failed batch.
 */
export interface BulkMutateResult<T> {
  ok: number;
  failed: number;
  okItems: T[];
  failedItems: T[];
}

export async function bulkMutate<T>(
  items: T[],
  op: (item: T, index: number) => Promise<{ error?: unknown }>,
  concurrency = 8,
): Promise<BulkMutateResult<T>> {
  const results = await pLimit(concurrency, items, op);
  const okItems: T[] = [];
  const failedItems: T[] = [];
  results.forEach((result, i) => {
    if (result?.error) failedItems.push(items[i]!);
    else okItems.push(items[i]!);
  });
  return { ok: okItems.length, failed: failedItems.length, okItems, failedItems };
}

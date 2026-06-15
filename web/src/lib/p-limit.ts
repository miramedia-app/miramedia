/**
 * Bounded-concurrency runner. `pLimit(concurrency, items, fn)` runs `fn`
 * over every item with at most `concurrency` promises in flight at once,
 * preserving result order. Avoids unbounded fan-out on bulk actions.
 */
export async function pLimit<T, R>(
  concurrency: number,
  items: T[],
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) return [];
  const results = new Array<R>(items.length);
  let cursor = 0;
  const worker = async () => {
    while (true) {
      const i = cursor++;
      if (i >= items.length) return;
      results[i] = await fn(items[i]!, i);
    }
  };
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, worker);
  await Promise.all(workers);
  return results;
}

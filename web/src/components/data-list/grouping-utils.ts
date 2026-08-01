/**
 * Counts how many items fall into each group over an arbitrary item list.
 *
 * DataList paginates first and then groups the current page, so a group header
 * would otherwise report only the members that happen to land on this page.
 * Running this over the full filtered set gives the honest total, which the
 * header renders as "N of M" when the page shows fewer.
 */
export function countGroups<T>(
  items: readonly T[],
  getGroup: (item: T) => { key: string },
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const item of items) {
    const { key } = getGroup(item);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

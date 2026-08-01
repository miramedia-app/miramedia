/**
 * Expanded-row state encoding, shared by `DataList` and `DataListSection`.
 *
 * The row set holds two kinds of entries:
 * - bare `id`      → the row was explicitly expanded
 * - `__c:${id}`    → the row was explicitly collapsed (only meaningful when
 *                    `defaultExpanded` is set)
 * - neither        → follow `defaultExpanded`
 *
 * The collapse sentinel is what makes `defaultExpanded` collapsible without
 * seeding state from `data` — seeding would reset on every refetch of an
 * SSE-driven list.
 */
export function collapseKey(id: string): string {
  return `__c:${id}`;
}

/** Whether a row renders expanded, given the current row set. */
export function isRowExpanded(
  rows: ReadonlySet<string>,
  id: string,
  defaultExpanded: boolean,
): boolean {
  if (rows.has(id)) return true;
  return defaultExpanded && !rows.has(collapseKey(id));
}

/**
 * Next row set after toggling `id`. Never leaves both the bare id and its
 * collapse sentinel present.
 */
export function nextExpandedRows(
  prev: ReadonlySet<string>,
  id: string,
  defaultExpanded: boolean,
): Set<string> {
  const next = new Set(prev);
  const collapsed = collapseKey(id);
  if (isRowExpanded(prev, id, defaultExpanded)) {
    next.delete(id);
    if (defaultExpanded) next.add(collapsed);
  } else {
    next.delete(collapsed);
    next.add(id);
  }
  return next;
}

/**
 * Compute the id a keyboard move should land on.
 *
 * Focus is tracked by id, so the index is re-derived against the *current*
 * `visibleIds` every move. When `currentId` is no longer present (search / sort
 * / page / SSE churn dropped it), the move restarts from the top — index −1 for
 * a downward move (lands on 0) and index 0 for an upward move (stays on 0).
 * Returns `null` only when there are no rows to focus.
 */
export function nextFocusId(
  visibleIds: readonly string[],
  currentId: string | null,
  delta: 1 | -1,
): string | null {
  if (visibleIds.length === 0) return null;
  const idx = currentId != null ? visibleIds.indexOf(currentId) : -1;
  const from = idx < 0 ? (delta > 0 ? -1 : 0) : idx;
  const next = Math.min(Math.max(from + delta, 0), visibleIds.length - 1);
  return visibleIds[next] ?? null;
}

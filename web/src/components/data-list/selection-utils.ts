/**
 * Pure selection helpers for the data list. Kept free of React imports so the
 * Node-only vitest lane can import them directly.
 */

/**
 * Intersect a selection with the current id universe (the filtered set).
 *
 * Returns `null` when every selected id still exists, so callers can preserve
 * the previous Set identity and avoid a pointless re-render.
 */
export function pruneSelection(
  selected: ReadonlySet<string>,
  universe: readonly string[],
): Set<string> | null {
  const keep = new Set(universe);
  let changed = false;
  const next = new Set<string>();
  for (const id of selected) {
    if (keep.has(id)) next.add(id);
    else changed = true;
  }
  return changed ? next : null;
}

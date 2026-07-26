"use client";

import * as React from "react";

export interface ListSelectionState {
  selected: Set<string>;
  isSelected: (id: string) => boolean;
  toggle: (id: string, opts?: { shift?: boolean }) => void;
  selectRange: (fromId: string, toId: string) => void;
  selectAll: () => void;
  clear: () => void;
  count: number;
  anchorId: string | null;
}

export interface UseListSelectionOptions {
  /** Ordered list of selectable ids (filtered + paginated view order). */
  ids: string[];
  /**
   * Full filtered id list across every page. `selectAll` operates on this so
   * "Select all N" selects N rows, not just the current page. Defaults to
   * `ids`; range/shift selection always stays page-scoped.
   */
  allIds?: string[];
  /** Ids that cannot be selected (e.g. current user). */
  disabledIds?: Set<string>;
}

/**
 * Header checkbox state for a selection, over the full filtered id list.
 * Pure so it can be unit-tested outside a DOM environment.
 */
export function selectionHeaderState(
  ids: string[],
  disabledIds: Set<string> | undefined,
  selected: Set<string>,
): { allSelected: boolean; someSelected: boolean } {
  let selectable = 0;
  let selectedCount = 0;
  for (const id of ids) {
    if (disabledIds?.has(id)) continue;
    selectable++;
    if (selected.has(id)) selectedCount++;
  }
  return {
    allSelected: selectable > 0 && selectedCount === selectable,
    someSelected: selectedCount > 0 && selectedCount < selectable,
  };
}

export function useListSelection({
  ids,
  allIds,
  disabledIds,
}: UseListSelectionOptions): ListSelectionState {
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [anchorId, setAnchorId] = React.useState<string | null>(null);

  const idIndex = React.useMemo(() => {
    const m = new Map<string, number>();
    ids.forEach((id, i) => m.set(id, i));
    return m;
  }, [ids]);

  const isDisabled = React.useCallback(
    (id: string) => disabledIds?.has(id) ?? false,
    [disabledIds],
  );

  const toggle = React.useCallback(
    (id: string, opts?: { shift?: boolean }) => {
      if (isDisabled(id)) return;
      if (opts?.shift && anchorId != null) {
        const from = idIndex.get(anchorId);
        const to = idIndex.get(id);
        if (from != null && to != null) {
          const [lo, hi] = from < to ? [from, to] : [to, from];
          setSelected((prev) => {
            const next = new Set(prev);
            for (let i = lo; i <= hi; i++) {
              const rid = ids[i];
              if (rid && !isDisabled(rid)) next.add(rid);
            }
            return next;
          });
          setAnchorId(id);
          return;
        }
      }
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      setAnchorId(id);
    },
    [anchorId, idIndex, ids, isDisabled],
  );

  const selectRange = React.useCallback(
    (fromId: string, toId: string) => {
      const from = idIndex.get(fromId);
      const to = idIndex.get(toId);
      if (from == null || to == null) return;
      const [lo, hi] = from < to ? [from, to] : [to, from];
      setSelected((prev) => {
        const next = new Set(prev);
        for (let i = lo; i <= hi; i++) {
          const rid = ids[i];
          if (rid && !isDisabled(rid)) next.add(rid);
        }
        return next;
      });
    },
    [idIndex, ids, isDisabled],
  );

  const selectAll = React.useCallback(() => {
    const source = allIds ?? ids;
    setSelected(new Set(source.filter((id) => !isDisabled(id))));
  }, [allIds, ids, isDisabled]);

  const clear = React.useCallback(() => setSelected(new Set()), []);

  // Stable wrapper around `selected.has` — `selection.isSelected` retains
  // identity across renders so downstream useMemos (selectedItems, the
  // header allSelected check) don't invalidate on every parent render.
  const selectedRef = React.useRef(selected);
  selectedRef.current = selected;
  const isSelected = React.useCallback((id: string) => selectedRef.current.has(id), []);

  // Memoize the public state object so consumers see stable identity when
  // nothing changed. Without this, every render allocates a fresh struct
  // and `[..., selection]` deps invalidate constantly.
  return React.useMemo(
    () => ({
      selected,
      isSelected,
      toggle,
      selectRange,
      selectAll,
      clear,
      count: selected.size,
      anchorId,
    }),
    [selected, isSelected, toggle, selectRange, selectAll, clear, anchorId],
  );
}

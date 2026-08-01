"use client";

import * as React from "react";

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/**
 * Content slots whose open popup owns the keyboard and must disarm the list
 * hotkeys. Two families qualify:
 *   - Overlays that cover the list (`dialog`/`sheet`/`alert-dialog`).
 *   - Base UI select/combobox/menu popups: while one is open, focus sits on a
 *     non-typing element, so unguarded ArrowUp/Down would move the LIST cursor
 *     instead of the popup's active item and `x`/`f` would mutate the list
 *     behind the popup.
 *
 * The `popover-content` slot is deliberately excluded: the list's own filter
 * popover must keep the hotkeys armed, or `f` would become one-shot.
 *
 * These are all Base UI (not Radix), so there is no `aria-modal`; the repo's
 * `data-slot` attributes are the reliable hook. Base UI Portals unmount the
 * popup when closed, so the element is simply absent while shut — the
 * `:not([data-closed])` guard only matters during the brief close animation
 * (combobox emits `data-closed`; select uses `data-state="closed"` so the
 * guard is a harmless belt-and-suspenders there).
 */
export const MODAL_SELECTOR = [
  '[data-slot="dialog-content"]',
  '[data-slot="sheet-content"]',
  '[data-slot="alert-dialog-content"]',
  '[data-slot="select-content"]',
  '[data-slot="combobox-content"]',
  '[data-slot="dropdown-menu-content"]',
  '[data-slot="dropdown-menu-sub-content"]',
]
  .map((s) => `${s}:not([data-closed])`)
  .join(", ");

/**
 * True while a keyboard-owning popup (dialog, sheet, alert dialog, or an open
 * select/combobox/menu) is mounted anywhere on the page.
 */
export function isModalOpen(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector(MODAL_SELECTOR) != null;
}

/** Handler key the current keypress maps to, or `null` for no-op. */
export type HotkeyAction =
  | "onSelectAll"
  | "onMoveDown"
  | "onMoveUp"
  | "onRangeExtendDown"
  | "onRangeExtendUp"
  | "onToggleSelect"
  | "onOpen"
  | "onFocusSearch"
  | "onOpenFilter"
  | "onCreate"
  | "onClear";

export type HotkeyEvent = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "shiftKey">;

/**
 * Pure decision for a keypress. Kept separate from the DOM wiring so it can be
 * unit-tested without a browser environment. `modalOpen` short-circuits
 * everything (an open dialog / select / combobox / menu owns the keyboard);
 * `typing` routes keys to a focused text field instead of the list.
 */
export function resolveHotkey(
  e: HotkeyEvent,
  opts: { modalOpen: boolean; typing: boolean },
): { action: HotkeyAction | null; preventDefault: boolean } {
  const none = { action: null, preventDefault: false } as const;
  if (opts.modalOpen) return none;
  if (e.metaKey || e.ctrlKey) {
    if ((e.key === "a" || e.key === "A") && !opts.typing) {
      return { action: "onSelectAll", preventDefault: true };
    }
    return none;
  }
  if (opts.typing) {
    return e.key === "Escape" ? { action: "onClear", preventDefault: false } : none;
  }
  switch (e.key) {
    case "j":
    case "ArrowDown":
      return e.shiftKey
        ? { action: "onRangeExtendDown", preventDefault: true }
        : { action: "onMoveDown", preventDefault: true };
    case "k":
    case "ArrowUp":
      return e.shiftKey
        ? { action: "onRangeExtendUp", preventDefault: true }
        : { action: "onMoveUp", preventDefault: true };
    case "x":
      return { action: "onToggleSelect", preventDefault: true };
    case "Enter":
      return { action: "onOpen", preventDefault: false };
    case "/":
      return { action: "onFocusSearch", preventDefault: true };
    case "f":
      return { action: "onOpenFilter", preventDefault: true };
    case "c":
      return { action: "onCreate", preventDefault: false };
    case "Escape":
      return { action: "onClear", preventDefault: false };
    default:
      return none;
  }
}

export interface ListHotkeyHandlers {
  onMoveDown?: () => void;
  onMoveUp?: () => void;
  onToggleSelect?: () => void;
  onSelectAll?: () => void;
  onClear?: () => void;
  onOpen?: () => void;
  onFocusSearch?: () => void;
  onOpenFilter?: () => void;
  onCreate?: () => void;
  onRangeExtendDown?: () => void;
  onRangeExtendUp?: () => void;
}

export function useListHotkeys(handlers: ListHotkeyHandlers, enabled = true) {
  const ref = React.useRef(handlers);
  React.useEffect(() => {
    ref.current = handlers;
  });

  React.useEffect(() => {
    if (!enabled) return;
    function onKey(e: KeyboardEvent) {
      // A modal or an open select/combobox/menu popup owns the keyboard: the
      // list is not the active surface, so `x`/`f`/`c` must not act on it,
      // arrows must reach the popup's items, Escape must only close the popup,
      // and Cmd/Ctrl+A must fall through to native text selection.
      const { action, preventDefault } = resolveHotkey(e, {
        modalOpen: isModalOpen(),
        typing: isTypingTarget(e.target),
      });
      if (!action) return;
      if (preventDefault) e.preventDefault();
      ref.current[action]?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

"use client";

import * as React from "react";

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
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
      if (e.metaKey || e.ctrlKey) {
        if ((e.key === "a" || e.key === "A") && !isTypingTarget(e.target)) {
          e.preventDefault();
          ref.current.onSelectAll?.();
        }
        return;
      }
      if (isTypingTarget(e.target)) {
        if (e.key === "Escape") ref.current.onClear?.();
        return;
      }
      switch (e.key) {
        case "j":
        case "ArrowDown":
          if (e.shiftKey) {
            e.preventDefault();
            ref.current.onRangeExtendDown?.();
          } else {
            e.preventDefault();
            ref.current.onMoveDown?.();
          }
          break;
        case "k":
        case "ArrowUp":
          if (e.shiftKey) {
            e.preventDefault();
            ref.current.onRangeExtendUp?.();
          } else {
            e.preventDefault();
            ref.current.onMoveUp?.();
          }
          break;
        case "x":
          e.preventDefault();
          ref.current.onToggleSelect?.();
          break;
        case "Enter":
          ref.current.onOpen?.();
          break;
        case "/":
          e.preventDefault();
          ref.current.onFocusSearch?.();
          break;
        case "f":
          e.preventDefault();
          ref.current.onOpenFilter?.();
          break;
        case "c":
          ref.current.onCreate?.();
          break;
        case "Escape":
          ref.current.onClear?.();
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

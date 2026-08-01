import { describe, expect, it } from "vitest";
import { MODAL_SELECTOR, isModalOpen, resolveHotkey, type HotkeyEvent } from "./use-list-hotkeys";

// The vitest environment is Node (no DOM, and jsdom is intentionally not a
// dependency), so the DOM-dependent branch of `isModalOpen` is exercised via
// the pure `MODAL_SELECTOR` composition and `resolveHotkey` decision instead.
// The live modal-vs-popover behaviour is covered by the manual smoke matrix —
// see plans/202.

describe("MODAL_SELECTOR", () => {
  const parts = MODAL_SELECTOR.split(",").map((s) => s.trim());

  it.each([
    "dialog-content",
    "sheet-content",
    "alert-dialog-content",
    "select-content",
    "combobox-content",
    "dropdown-menu-content",
    "dropdown-menu-sub-content",
  ])("disarms hotkeys for an open %s popup", (slot) => {
    expect(parts).toContain(`[data-slot="${slot}"]:not([data-closed])`);
  });

  it("excludes the list's own filter popover so `f` stays armed", () => {
    // Deliberate exclusion — popovers must NOT disarm the hotkeys.
    expect(MODAL_SELECTOR).not.toContain("popover-content");
  });

  it("guards every surface against the close animation with :not([data-closed])", () => {
    for (const part of parts) expect(part.endsWith(":not([data-closed])")).toBe(true);
  });
});

describe("isModalOpen", () => {
  it("reports no modal when there is no document", () => {
    expect(typeof document).toBe("undefined");
    expect(isModalOpen()).toBe(false);
  });
});

function ev(key: string, mods: Partial<HotkeyEvent> = {}): HotkeyEvent {
  return { key, metaKey: false, ctrlKey: false, shiftKey: false, ...mods };
}

describe("resolveHotkey", () => {
  const open = { modalOpen: false, typing: false };

  it("dispatches `x` to onToggleSelect on the list surface", () => {
    expect(resolveHotkey(ev("x"), open)).toEqual({
      action: "onToggleSelect",
      preventDefault: true,
    });
  });

  it("swallows every key while a modal/popup is open", () => {
    for (const key of ["x", "f", "j", "ArrowUp", "Enter", "Escape"]) {
      expect(resolveHotkey(ev(key), { modalOpen: true, typing: false })).toEqual({
        action: null,
        preventDefault: false,
      });
    }
  });

  it("also swallows Cmd+A while a modal is open", () => {
    expect(resolveHotkey(ev("a", { metaKey: true }), { modalOpen: true, typing: false })).toEqual({
      action: null,
      preventDefault: false,
    });
  });

  it("maps arrows and vim keys, with shift extending the range", () => {
    expect(resolveHotkey(ev("ArrowDown"), open).action).toBe("onMoveDown");
    expect(resolveHotkey(ev("j"), open).action).toBe("onMoveDown");
    expect(resolveHotkey(ev("ArrowUp"), open).action).toBe("onMoveUp");
    expect(resolveHotkey(ev("k", { shiftKey: true }), open).action).toBe("onRangeExtendUp");
    expect(resolveHotkey(ev("ArrowDown", { shiftKey: true }), open).action).toBe(
      "onRangeExtendDown",
    );
  });

  it("routes Cmd/Ctrl+A to select-all only when not typing", () => {
    expect(resolveHotkey(ev("a", { metaKey: true }), open).action).toBe("onSelectAll");
    expect(
      resolveHotkey(ev("a", { ctrlKey: true }), { modalOpen: false, typing: true }).action,
    ).toBe(null);
  });

  it("in a text field, only Escape acts (clearing) and never prevents default", () => {
    const typing = { modalOpen: false, typing: true };
    expect(resolveHotkey(ev("x"), typing).action).toBe(null);
    expect(resolveHotkey(ev("Escape"), typing)).toEqual({
      action: "onClear",
      preventDefault: false,
    });
  });
});

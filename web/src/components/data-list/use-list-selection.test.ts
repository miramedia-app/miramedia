import { describe, expect, it } from "vitest";

import { selectionHeaderState } from "./use-list-selection";

describe("selectionHeaderState", () => {
  it("reports neither state when nothing is selected", () => {
    expect(selectionHeaderState(["a", "b", "c"], undefined, new Set())).toEqual({
      allSelected: false,
      someSelected: false,
    });
  });

  it("reports indeterminate when some are selected", () => {
    expect(selectionHeaderState(["a", "b", "c"], undefined, new Set(["a"]))).toEqual({
      allSelected: false,
      someSelected: true,
    });
  });

  it("reports all-selected when every selectable id is selected, ignoring disabled ids", () => {
    expect(selectionHeaderState(["a", "b", "c"], new Set(["c"]), new Set(["a", "b"]))).toEqual({
      allSelected: true,
      someSelected: false,
    });
  });

  it("reports neither state for an empty list", () => {
    expect(selectionHeaderState([], undefined, new Set())).toEqual({
      allSelected: false,
      someSelected: false,
    });
  });

  it("reports neither state when every id is disabled", () => {
    expect(selectionHeaderState(["a", "b"], new Set(["a", "b"]), new Set())).toEqual({
      allSelected: false,
      someSelected: false,
    });
  });
});

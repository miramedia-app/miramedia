import { describe, expect, it } from "vitest";

import { pruneSelection } from "./selection-utils";

describe("pruneSelection", () => {
  it("returns null when the selection is a subset of the universe", () => {
    expect(pruneSelection(new Set(["a", "c"]), ["a", "b", "c"])).toBeNull();
  });

  it("drops selected ids that are no longer in the universe", () => {
    expect(pruneSelection(new Set(["a", "b", "c"]), ["b"])).toEqual(new Set(["b"]));
  });

  it("returns an empty set when the universe is empty and the selection is not", () => {
    expect(pruneSelection(new Set(["a", "b"]), [])).toEqual(new Set());
  });

  it("returns null for an empty selection", () => {
    expect(pruneSelection(new Set(), ["a", "b"])).toBeNull();
    expect(pruneSelection(new Set(), [])).toBeNull();
  });
});

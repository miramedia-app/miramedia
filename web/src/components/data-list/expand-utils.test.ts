import { describe, expect, it } from "vitest";
import { collapseKey, isRowExpanded, nextExpandedRows } from "./expand-utils";

describe("isRowExpanded", () => {
  it("follows the row set when defaultExpanded is off", () => {
    expect(isRowExpanded(new Set(["a"]), "a", false)).toBe(true);
    expect(isRowExpanded(new Set(), "a", false)).toBe(false);
  });

  it("defaults to expanded until a collapse sentinel is present", () => {
    expect(isRowExpanded(new Set(), "a", true)).toBe(true);
    expect(isRowExpanded(new Set([collapseKey("a")]), "a", true)).toBe(false);
  });
});

describe("nextExpandedRows", () => {
  it("collapses a default-expanded row by writing the sentinel", () => {
    const next = nextExpandedRows(new Set(), "a", true);
    expect(isRowExpanded(next, "a", true)).toBe(false);
    expect(next.has(collapseKey("a"))).toBe(true);
  });

  it("re-expands by removing the sentinel", () => {
    const collapsed = nextExpandedRows(new Set(), "a", true);
    const reexpanded = nextExpandedRows(collapsed, "a", true);
    expect(isRowExpanded(reexpanded, "a", true)).toBe(true);
    expect(reexpanded.has(collapseKey("a"))).toBe(false);
  });

  it("keeps default-off behaviour unchanged", () => {
    const expanded = nextExpandedRows(new Set(), "a", false);
    expect(expanded.has("a")).toBe(true);
    expect(expanded.has(collapseKey("a"))).toBe(false);
    const collapsed = nextExpandedRows(expanded, "a", false);
    expect(collapsed.has("a")).toBe(false);
    expect(collapsed.has(collapseKey("a"))).toBe(false);
  });

  it("never leaves both the bare id and the sentinel present", () => {
    let rows: Set<string> = new Set();
    for (const defaultExpanded of [true, false, true, true, false]) {
      rows = nextExpandedRows(rows, "a", defaultExpanded);
      expect(rows.has("a") && rows.has(collapseKey("a"))).toBe(false);
    }
  });

  it("does not mutate the previous set", () => {
    const prev = new Set(["a"]);
    nextExpandedRows(prev, "a", false);
    expect(prev.has("a")).toBe(true);
  });
});

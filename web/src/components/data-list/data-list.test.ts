import { describe, expect, it } from "vitest";

import {
  computePaginationPages,
  isServerPaginationTotalKnown,
  shouldSnapPaginationPage,
} from "@/components/data-list/data-list";

// Node-only vitest (no jsdom) — exercise the server-paged snap/pagination helpers
// directly instead of rendering the full DataList tree.
describe("server-paged pagination helpers", () => {
  it("treats an unknown server total as not yet known", () => {
    expect(isServerPaginationTotalKnown(true, undefined)).toBe(false);
    expect(shouldSnapPaginationPage(3, 50, undefined, true, true)).toBe(false);
  });

  it("snaps past the end when the server reports an empty library", () => {
    expect(isServerPaginationTotalKnown(true, 0)).toBe(true);
    expect(shouldSnapPaginationPage(3, 50, 0, true, true)).toBe(true);
    expect(computePaginationPages(0, 50)).toBe(1);
  });

  it("computes page count from a known server total", () => {
    expect(computePaginationPages(120, 50)).toBe(3);
    expect(shouldSnapPaginationPage(3, 50, 120, true, true)).toBe(false);
    expect(shouldSnapPaginationPage(4, 50, 120, true, true)).toBe(true);
  });
});

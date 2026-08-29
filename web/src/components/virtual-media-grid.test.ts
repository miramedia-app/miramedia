import { describe, expect, it } from "vitest";
import {
  MEDIA_GRID_BREAKPOINT_COLUMNS,
  MEDIA_GRID_COLUMNS_CLASS,
} from "@/components/virtual-media-grid";

/** The virtualizer chunks rows in JS; the CSS ladder must match exactly. */
describe("media grid column ladder", () => {
  it("keeps Tailwind classes in sync with JS breakpoint column counts", () => {
    const classes = MEDIA_GRID_COLUMNS_CLASS.split(/\s+/);
    for (const [bp, cols] of Object.entries(MEDIA_GRID_BREAKPOINT_COLUMNS)) {
      const expected = bp === "base" ? `grid-cols-${cols}` : `${bp}:grid-cols-${cols}`;
      expect(classes).toContain(expected);
    }
    expect(classes).toHaveLength(Object.keys(MEDIA_GRID_BREAKPOINT_COLUMNS).length);
  });
});

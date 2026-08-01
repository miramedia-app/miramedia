import { describe, expect, it } from "vitest";
import { nextFocusId } from "./focus-utils";

describe("nextFocusId", () => {
  const ids = ["a", "b", "c"];

  it("returns null when there are no rows", () => {
    expect(nextFocusId([], null, 1)).toBe(null);
    expect(nextFocusId([], "a", -1)).toBe(null);
  });

  it("starts at the top from no focus", () => {
    expect(nextFocusId(ids, null, 1)).toBe("a");
    expect(nextFocusId(ids, null, -1)).toBe("a");
  });

  it("moves down and clamps at the last row", () => {
    expect(nextFocusId(ids, "a", 1)).toBe("b");
    expect(nextFocusId(ids, "c", 1)).toBe("c");
  });

  it("moves up and clamps at the first row", () => {
    expect(nextFocusId(ids, "c", -1)).toBe("b");
    expect(nextFocusId(ids, "a", -1)).toBe("a");
  });

  it("resets to the top when the focused id is no longer visible", () => {
    // The stale id ("z") is treated as no focus, so the next move restarts from
    // the top instead of landing on whatever row inherited its old index.
    expect(nextFocusId(ids, "z", 1)).toBe("a");
    expect(nextFocusId(ids, "z", -1)).toBe("a");
  });
});

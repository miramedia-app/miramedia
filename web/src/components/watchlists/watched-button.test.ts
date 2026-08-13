import { describe, expect, it } from "vitest";

import { getWatchedButtonA11y } from "@/components/watchlists/watched-button";

describe("getWatchedButtonA11y", () => {
  it("exposes pressed state and explicit mark labels", () => {
    expect(getWatchedButtonA11y(false)).toEqual({
      "aria-pressed": false,
      label: "Mark watched",
    });
    expect(getWatchedButtonA11y(true)).toEqual({
      "aria-pressed": true,
      label: "Mark unwatched",
    });
  });
});

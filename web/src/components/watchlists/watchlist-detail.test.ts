import { describe, expect, it } from "vitest";

import {
  getMoveControlState,
  getWatchlistDetailViewState,
} from "@/components/watchlists/watchlist-detail";

describe("getWatchlistDetailViewState", () => {
  it("maps detail query states including not-found", () => {
    expect(
      getWatchlistDetailViewState({
        isPending: true,
        isError: false,
        error: null,
        itemCount: 0,
      }),
    ).toBe("pending");
    expect(
      getWatchlistDetailViewState({
        isPending: false,
        isError: true,
        error: { status: 404 },
        itemCount: 0,
      }),
    ).toBe("not-found");
    expect(
      getWatchlistDetailViewState({
        isPending: false,
        isError: false,
        error: null,
        itemCount: 0,
      }),
    ).toBe("empty");
    expect(
      getWatchlistDetailViewState({
        isPending: false,
        isError: false,
        error: null,
        itemCount: 1,
      }),
    ).toBe("ready");
  });
});

describe("getMoveControlState", () => {
  it("disables move controls at the edges", () => {
    expect(getMoveControlState(0, 3, "up")).toEqual({ disabled: true, label: "Move up" });
    expect(getMoveControlState(2, 3, "down")).toEqual({ disabled: true, label: "Move down" });
    expect(getMoveControlState(1, 3, "up")).toEqual({ disabled: false, label: "Move up" });
  });
});

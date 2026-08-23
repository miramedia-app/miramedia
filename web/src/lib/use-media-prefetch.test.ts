import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createHoverIntent, DETAIL_BUNDLE_PREFETCH_INTENT_MS } from "./use-media-prefetch";

describe("detail-bundle hover intent", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not prefetch when enter then leave happens before 150ms", () => {
    const prefetch = vi.fn();
    const intent = createHoverIntent();

    intent.schedule(prefetch);
    vi.advanceTimersByTime(DETAIL_BUNDLE_PREFETCH_INTENT_MS - 1);
    intent.cancel();
    vi.advanceTimersByTime(50);

    expect(prefetch).not.toHaveBeenCalled();
  });

  it("prefetches once after 160ms of hover", () => {
    const prefetch = vi.fn();
    const intent = createHoverIntent();

    intent.schedule(prefetch);
    vi.advanceTimersByTime(160);

    expect(prefetch).toHaveBeenCalledOnce();
  });
});

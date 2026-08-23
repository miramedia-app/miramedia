import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cancelDebouncedInvalidate, debouncedInvalidate } from "./debounced-invalidate";

describe("debouncedInvalidate", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps independent timers when two QueryClients share a key", () => {
    const a = new QueryClient();
    const b = new QueryClient();
    const spyA = vi.spyOn(a, "invalidateQueries");
    const spyB = vi.spyOn(b, "invalidateQueries");
    const queryKey = ["torrents", "list"];

    debouncedInvalidate(a, queryKey, 400);
    debouncedInvalidate(b, queryKey, 400);
    vi.advanceTimersByTime(400);

    expect(spyA).toHaveBeenCalledOnce();
    expect(spyA).toHaveBeenCalledWith({ queryKey });
    expect(spyB).toHaveBeenCalledOnce();
    expect(spyB).toHaveBeenCalledWith({ queryKey });
  });

  it("cancelDebouncedInvalidate drops a pending timer for that client", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const queryKey = ["dashboard", "summary"];

    debouncedInvalidate(qc, queryKey, 400);
    cancelDebouncedInvalidate(qc, queryKey);
    vi.advanceTimersByTime(400);

    expect(spy).not.toHaveBeenCalled();
  });

  it("cancelDebouncedInvalidate without a key drops every pending timer", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");

    debouncedInvalidate(qc, ["torrents", "list"], 400);
    debouncedInvalidate(qc, ["dashboard", "summary"], 400);
    cancelDebouncedInvalidate(qc);
    vi.advanceTimersByTime(400);

    expect(spy).not.toHaveBeenCalled();
  });
});

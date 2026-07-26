import { describe, expect, it } from "vitest";

import { bulkMutate } from "@/lib/bulk-mutate";

describe("bulkMutate", () => {
  it("reports every item as ok when none return an error", async () => {
    const res = await bulkMutate([1, 2, 3], async () => ({ data: true, error: undefined }));
    expect(res).toEqual({ ok: 3, failed: 0, okItems: [1, 2, 3], failedItems: [] });
  });

  it("reports every item as failed when all return an error", async () => {
    const res = await bulkMutate(["a", "b"], async () => ({ error: { detail: "nope" } }));
    expect(res.ok).toBe(0);
    expect(res.failed).toBe(2);
    expect(res.failedItems).toEqual(["a", "b"]);
  });

  it("splits mixed results and preserves input order", async () => {
    const res = await bulkMutate([1, 2, 3, 4], async (n) =>
      n % 2 === 0 ? { error: "bad" } : { data: n },
    );
    expect(res.okItems).toEqual([1, 3]);
    expect(res.failedItems).toEqual([2, 4]);
    expect(res.ok).toBe(2);
    expect(res.failed).toBe(2);
  });

  it("never exceeds the concurrency limit", async () => {
    let inFlight = 0;
    let peak = 0;
    const items = Array.from({ length: 20 }, (_, i) => i);
    await bulkMutate(
      items,
      async () => {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 1));
        inFlight -= 1;
        return { data: true, error: undefined };
      },
      3,
    );
    expect(peak).toBeLessThanOrEqual(3);
    expect(peak).toBeGreaterThan(1);
  });

  it("returns empty counts for an empty item list", async () => {
    const res = await bulkMutate([], async () => ({ data: true, error: undefined }));
    expect(res).toEqual({ ok: 0, failed: 0, okItems: [], failedItems: [] });
  });
});

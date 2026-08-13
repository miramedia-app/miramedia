import { describe, expect, it, vi, beforeEach } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
  },
}));

import { fetchImportsPage, pruneQueuedScanIds } from "@/hooks/use-imports-queue";
import type { ImportItem } from "@/lib/imports";

function scanItem(id: string, status: "pending" | "queued" | "imported"): ImportItem {
  return {
    kind: "scan",
    id,
    result: {
      id,
      directory: `/lib/${id}`,
      detected_name: id,
      library_name: "lib",
      size_bytes: 0,
      file_count: 0,
      files: [],
      status,
      candidates: [],
      provider_candidates: [],
    },
  } as unknown as ImportItem;
}

describe("fetchImportsPage", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("requests a single page with tab/offset/limit and returns body total", async () => {
    const items = [scanItem("a", "pending")];
    mocks.get.mockResolvedValueOnce({
      data: { items, total: 321 },
      error: undefined,
    });

    const result = await fetchImportsPage("review", 3, 50);

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/api/v1/imports", {
      params: { query: { tab: "review", offset: 100, limit: 50 } },
      signal: undefined,
    });
    expect(result).toEqual({ items, total: 321 });
  });

  it("does not walk pages even when total far exceeds one page", async () => {
    const items = Array.from({ length: 50 }, (_, i) => scanItem(`s${i}`, "pending"));
    mocks.get.mockResolvedValueOnce({
      data: { items, total: 5000 },
      error: undefined,
    });

    const result = await fetchImportsPage("all", 1, 50);

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(result.items).toHaveLength(50);
    expect(result.total).toBe(5000);
  });

  it("rejects when openapi-fetch returns an error object", async () => {
    mocks.get.mockResolvedValueOnce({
      data: undefined,
      error: { detail: "boom" },
    });

    await expect(fetchImportsPage("review", 1, 50)).rejects.toEqual({ detail: "boom" });
  });

  it("returns empty items and total 0 for an empty page", async () => {
    mocks.get.mockResolvedValueOnce({
      data: { items: [], total: 0 },
      error: undefined,
    });

    const result = await fetchImportsPage("done", 1, 50);

    expect(result).toEqual({ items: [], total: 0 });
  });
});

describe("pruneQueuedScanIds", () => {
  it("keeps an id on the page with status pending", () => {
    const prev = new Set(["a"]);
    const next = pruneQueuedScanIds(prev, [scanItem("a", "pending")]);
    expect(next).toBe(prev);
    expect([...next]).toEqual(["a"]);
  });

  it("drops an id on the page with non-pending status", () => {
    const prev = new Set(["a", "b"]);
    const next = pruneQueuedScanIds(prev, [scanItem("a", "queued"), scanItem("b", "imported")]);
    expect(next).not.toBe(prev);
    expect([...next]).toEqual([]);
  });

  it("keeps an id absent from the current page (paging regression)", () => {
    const prev = new Set(["off-page"]);
    const next = pruneQueuedScanIds(prev, [scanItem("other", "pending")]);
    expect(next).toBe(prev);
    expect([...next]).toEqual(["off-page"]);
  });

  it("returns the same Set identity when nothing changes", () => {
    const prev = new Set(["a", "off"]);
    const next = pruneQueuedScanIds(prev, [scanItem("a", "pending")]);
    expect(next).toBe(prev);
  });
});

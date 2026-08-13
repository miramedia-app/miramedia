import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
  },
}));

import {
  fetchTorrentsPage,
  torrentsListRefetchInterval,
  type RichTorrent,
} from "@/hooks/use-torrents-list";

describe("fetchTorrentsPage", () => {
  it("requests a single page with limit/offset, live:true, and reads X-Total-Count", async () => {
    const items = [{ id: "a", title: "One" }];
    mocks.get.mockResolvedValueOnce({
      data: items,
      error: undefined,
      response: {
        headers: {
          get: (name: string) => (name === "x-total-count" ? "123" : null),
        },
      },
    });

    const result = await fetchTorrentsPage(3, 50);

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/api/v1/torrents", {
      signal: undefined,
      params: { query: { limit: 50, offset: 100, live: true } },
    });
    expect(result).toEqual({ items, total: 123 });
  });

  it("treats a missing X-Total-Count as unknown", async () => {
    const items = [
      { id: "a", title: "One" },
      { id: "b", title: "Two" },
    ];
    mocks.get.mockResolvedValueOnce({
      data: items,
      error: undefined,
      response: { headers: { get: () => null } },
    });

    const result = await fetchTorrentsPage(1, 50);

    expect(result.total).toBeNull();
    expect(result.items).toEqual(items);
  });

  it("treats a malformed X-Total-Count as unknown", async () => {
    const items = [{ id: "a", title: "One" }];
    mocks.get.mockResolvedValueOnce({
      data: items,
      error: undefined,
      response: {
        headers: {
          get: (name: string) => (name === "x-total-count" ? "abc" : null),
        },
      },
    });

    const result = await fetchTorrentsPage(1, 50);

    expect(result.total).toBeNull();
    expect(result.items).toEqual(items);
  });
});

function torrent(status: RichTorrent["status"]): RichTorrent {
  return { status, progress: 0, num_peers: 0, num_seeds: 0, download_speed: 0 } as RichTorrent;
}

describe("torrentsListRefetchInterval", () => {
  it("polls every 5s while a torrent on the page is downloading", () => {
    expect(
      torrentsListRefetchInterval({
        items: [torrent(1), torrent(2)],
        total: 2,
      }),
    ).toBe(5000);
  });

  it("falls back to 60s when nothing on the page is downloading", () => {
    expect(torrentsListRefetchInterval({ items: [torrent(1), torrent(3)], total: 2 })).toBe(60000);
    expect(torrentsListRefetchInterval({ items: [], total: 0 })).toBe(60000);
    expect(torrentsListRefetchInterval(undefined)).toBe(60000);
  });
});

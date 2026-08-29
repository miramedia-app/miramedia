// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
    POST: mocks.post,
    DELETE: vi.fn(),
    PUT: vi.fn(),
  },
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/hooks/use-bulk-torrent-actions", () => ({
  useBulkTorrentActions: () => ({
    bulkWorking: false,
    pause: vi.fn(),
    resume: vi.fn(),
    remove: vi.fn(),
    pauseOne: vi.fn(),
    resumeOne: vi.fn(),
    retryOne: vi.fn(),
  }),
}));

vi.mock("@/lib/api/media-queries", () => ({
  showDetailBundleQueryOptions: (showId: string) => ({
    queryKey: ["show", showId, "bundle"],
    queryFn: async () => ({
      show: {
        id: showId,
        name: "Test Show",
        overview: "",
        year: 2020,
        external_id: "ext",
        metadata_provider: "native",
        seasons: [
          {
            id: "season-a",
            number: 1,
            skipped: false,
            episodes: [
              {
                id: "episode-a1",
                number: 1,
                title: "Pilot",
                skipped: false,
              },
            ],
          },
          {
            id: "season-b",
            number: 2,
            skipped: false,
            episodes: [
              {
                id: "episode-b1",
                number: 1,
                title: "Second",
                skipped: false,
              },
            ],
          },
        ],
      },
      torrents: [],
      subtitles_by_episode: {},
    }),
  }),
}));

import { useShowDetail } from "@/hooks/use-show-detail";

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
}

function wrapperFor(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("useShowDetail season files", () => {
  beforeEach(() => {
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/api/v1/shows/{show_id}/torrents") {
        return { data: [], error: undefined };
      }
      return { data: undefined, error: undefined };
    });
    mocks.post.mockResolvedValue({
      data: {
        results: {
          "season-a": [
            {
              id: "file-a",
              episode_id: "episode-a1",
              quality: "fullhd",
              torrent_id: null,
            },
          ],
          "season-b": [
            {
              id: "file-b",
              episode_id: "episode-b1",
              quality: "fullhd",
              torrent_id: null,
            },
          ],
        },
        errors: {},
      },
      error: undefined,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("fetches expanded season files in one batch request and seeds per-season cache", async () => {
    const qc = createClient();
    const { result } = renderHook(() => useShowDetail("show-1"), {
      wrapper: wrapperFor(qc),
    });

    await waitFor(() => expect(result.current.show).toBeDefined());

    await act(async () => {
      result.current.toggleSeason("season-a");
      result.current.toggleSeason("season-b");
    });

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledTimes(1);
    });

    expect(mocks.post).toHaveBeenCalledWith("/api/v1/seasons/files/batch", {
      signal: expect.any(AbortSignal),
      body: {
        season_ids: ["season-a", "season-b"],
        show_id: "show-1",
      },
    });
    expect(mocks.get).not.toHaveBeenCalledWith(
      "/api/v1/seasons/{season_id}/files",
      expect.anything(),
    );

    await waitFor(() => {
      expect(qc.getQueryData(["season-files", "season-a"])).toBeDefined();
      expect(qc.getQueryData(["season-files", "season-b"])).toBeDefined();
    });
  });
});

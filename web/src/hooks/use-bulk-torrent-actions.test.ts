import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  bulkMutate: vi.fn(),
  delete: vi.fn(),
  error: vi.fn(),
  post: vi.fn(),
  setBulkWorking: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("react", () => ({
  useCallback: <T>(callback: T) => callback,
  useState: () => [false, mocks.setBulkWorking],
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    DELETE: mocks.delete,
    POST: mocks.post,
  },
}));

vi.mock("@/lib/bulk-mutate", () => ({
  bulkMutate: mocks.bulkMutate,
}));

vi.mock("sonner", () => ({
  toast: {
    error: mocks.error,
    success: mocks.success,
    warning: mocks.warning,
  },
}));

import { reportBulkResult, useBulkTorrentActions } from "@/hooks/use-bulk-torrent-actions";

describe("reportBulkResult", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reports an all-success result with the existing pluralization", () => {
    reportBulkResult(2, 0, "paused");

    expect(mocks.success).toHaveBeenCalledWith("2 torrents paused");
    expect(mocks.error).not.toHaveBeenCalled();
    expect(mocks.warning).not.toHaveBeenCalled();
  });

  it("reports an all-failure result with the existing action copy", () => {
    reportBulkResult(0, 2, "deleted");

    expect(mocks.error).toHaveBeenCalledWith("Failed to delete some torrents");
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.warning).not.toHaveBeenCalled();
  });

  it("reports a mixed result with the existing counts", () => {
    reportBulkResult(3, 1, "resumed");

    expect(mocks.warning).toHaveBeenCalledWith("3 resumed, 1 failed");
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });

  it("pluralizes the retried verb like the others", () => {
    reportBulkResult(1, 0, "retried");
    reportBulkResult(3, 0, "retried");

    expect(mocks.success).toHaveBeenCalledWith("1 torrent retried");
    expect(mocks.success).toHaveBeenCalledWith("3 torrents retried");
  });

  it("uses the retry action copy when every retry fails", () => {
    reportBulkResult(0, 2, "retried");

    expect(mocks.error).toHaveBeenCalledWith("Failed to retry some torrents");
  });

  it("preserves the torrents page action-specific punctuation", () => {
    const failurePunctuation = { failurePeriod: true };

    reportBulkResult(1, 0, "paused", failurePunctuation);
    reportBulkResult(1, 0, "deleted", { ...failurePunctuation, successPeriod: true });
    reportBulkResult(0, 1, "resumed", failurePunctuation);

    expect(mocks.success).toHaveBeenCalledWith("1 torrent paused");
    expect(mocks.success).toHaveBeenCalledWith("1 torrent deleted.");
    expect(mocks.error).toHaveBeenCalledWith("Failed to resume some torrents.");
  });
});

describe("useBulkTorrentActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("runs an action, reports its result, invalidates, and toggles working state", async () => {
    const result = {
      ok: 1,
      failed: 1,
      okItems: ["one"],
      failedItems: ["two"],
    };
    mocks.bulkMutate.mockResolvedValue(result);
    const invalidateAll = vi.fn().mockResolvedValue(undefined);
    const actions = useBulkTorrentActions(invalidateAll);

    await expect(actions.pause(["one", "two"])).resolves.toEqual(result);

    expect(mocks.bulkMutate).toHaveBeenCalledWith(["one", "two"], expect.any(Function));
    expect(mocks.warning).toHaveBeenCalledWith("1 paused, 1 failed");
    expect(invalidateAll).toHaveBeenCalledOnce();
    expect(mocks.setBulkWorking.mock.calls).toEqual([[true], [false]]);
  });

  it("passes delete options through and runs the result callback before invalidating", async () => {
    const result = {
      ok: 1,
      failed: 1,
      okItems: ["one"],
      failedItems: ["two"],
    };
    mocks.bulkMutate.mockImplementation(async (ids, operation) => {
      await operation(ids[0], 0);
      return result;
    });
    const callOrder: string[] = [];
    mocks.delete.mockResolvedValue({ error: undefined });
    const invalidateAll = vi.fn(async () => {
      callOrder.push("invalidate");
    });
    const onResult = vi.fn(() => {
      callOrder.push("result");
    });
    const actions = useBulkTorrentActions(invalidateAll, {
      deleteSuccessPeriod: true,
      failurePeriod: true,
    });

    await actions.remove(["one", "two"], { blockHash: true, onResult });

    expect(mocks.delete).toHaveBeenCalledWith("/api/v1/torrents/{torrent_id}", {
      params: {
        path: { torrent_id: "one" },
        query: { block_hash: true },
      },
    });
    expect(onResult).toHaveBeenCalledWith(result);
    expect(callOrder).toEqual(["result", "invalidate"]);
    expect(mocks.warning).toHaveBeenCalledWith("1 deleted, 1 failed");
  });

  it("maps the retried verb to the retry endpoint via the single-id wrapper", async () => {
    const result = { ok: 1, failed: 0, okItems: ["one"], failedItems: [] };
    mocks.bulkMutate.mockImplementation(async (ids, operation) => {
      await operation(ids[0], 0);
      return result;
    });
    mocks.post.mockResolvedValue({ error: undefined });
    const invalidateAll = vi.fn().mockResolvedValue(undefined);
    const actions = useBulkTorrentActions(invalidateAll);

    await expect(actions.retryOne("one")).resolves.toEqual(result);

    expect(mocks.bulkMutate).toHaveBeenCalledWith(["one"], expect.any(Function));
    expect(mocks.post).toHaveBeenCalledWith("/api/v1/torrents/{torrent_id}/retry", {
      params: { path: { torrent_id: "one" } },
    });
    expect(mocks.success).toHaveBeenCalledWith("1 torrent retried");
    expect(invalidateAll).toHaveBeenCalledOnce();
  });

  it("does nothing for an empty selection", async () => {
    const invalidateAll = vi.fn().mockResolvedValue(undefined);
    const actions = useBulkTorrentActions(invalidateAll);

    await expect(actions.resume([])).resolves.toBeNull();

    expect(mocks.bulkMutate).not.toHaveBeenCalled();
    expect(invalidateAll).not.toHaveBeenCalled();
    expect(mocks.setBulkWorking).not.toHaveBeenCalled();
  });

  it("reports thrown failures and always clears working state", async () => {
    mocks.bulkMutate.mockRejectedValue(new Error("network"));
    const invalidateAll = vi.fn().mockResolvedValue(undefined);
    const actions = useBulkTorrentActions(invalidateAll, { failurePeriod: true });

    await expect(actions.resume(["one"])).resolves.toBeNull();

    expect(mocks.error).toHaveBeenCalledWith("Failed to resume some torrents.");
    expect(invalidateAll).not.toHaveBeenCalled();
    expect(mocks.setBulkWorking.mock.calls).toEqual([[true], [false]]);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
    PUT: mocks.put,
    DELETE: mocks.delete,
  },
}));

import { createProgressReporter, REPORT_INTERVAL_MS } from "@/hooks/use-playback-progress";

function deferred() {
  let resolve!: (value?: unknown) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<unknown>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
}

function makeReporter(
  put: (body: {
    file_id: string;
    media_kind: "movie" | "episode";
    position_ms: number;
    duration_ms: number;
  }) => void | Promise<unknown>,
  clear: () => Promise<{ error?: unknown }> = async () => ({}),
  onTerminalPut?: () => void,
) {
  return createProgressReporter({
    put,
    clear,
    fileId: "file-1",
    mediaKind: "movie",
    onTerminalPut,
  });
}

describe("createProgressReporter", () => {
  const fileId = "file-1";
  const mediaKind = "movie" as const;

  beforeEach(() => {
    vi.useFakeTimers();
    mocks.put.mockReset();
    mocks.put.mockResolvedValue({ data: null, error: undefined });
    mocks.delete.mockReset();
    mocks.delete.mockResolvedValue({ data: undefined, error: undefined });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends the first report immediately and trailing-throttles later samples", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 1_000,
      duration_ms: 60_000,
    });
    await flushMicrotasks();

    reporter.report(2_000, 60_000);
    reporter.report(3_500, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(REPORT_INTERVAL_MS);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 3_500,
      duration_ms: 60_000,
    });

    reporter.dispose();
  });

  it("flush sends the pending sample at once and resets the throttle", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    await flushMicrotasks();
    reporter.report(4_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    reporter.flush("pause");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 4_000,
      duration_ms: 60_000,
    });

    reporter.report(5_000, 60_000);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(3);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 5_000,
      duration_ms: 60_000,
    });

    reporter.dispose();
  });

  it("flush with nothing new since the last send does not PUT again", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    reporter.flush("close");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    reporter.dispose();
  });

  it("never PUTs when durationMs is NaN or below 1000", () => {
    const put = vi.fn();
    const reporter = makeReporter(put);

    reporter.report(1_000, Number.NaN);
    reporter.report(1_000, 0);
    reporter.report(1_000, 999);
    reporter.flush("pause");

    expect(put).not.toHaveBeenCalled();
    reporter.dispose();
  });

  it("clamps positionMs greater than durationMs in the PUT body", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(90_000, 60_000);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 60_000,
      duration_ms: 60_000,
    });

    reporter.dispose();
  });

  it("rounds fractional ms so the PUT body stays integer-only", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    // video.currentTime * 1000 / video.duration * 1000 are fractional; the API
    // rejects floats with 422 int_from_float.
    reporter.report(3319.4089999999997, 3600499.9);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 3319,
      duration_ms: 3600500,
    });

    reporter.dispose();
  });

  it("rounds a fractional position clamped to a fractional duration", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(60000.9, 60000.4);
    await flushMicrotasks();
    const body = put.mock.calls[0][0];
    expect(body.position_ms).toBe(60_000);
    expect(body.duration_ms).toBe(60_000);
    expect(Number.isInteger(body.position_ms)).toBe(true);
    expect(Number.isInteger(body.duration_ms)).toBe(true);

    reporter.dispose();
  });

  it("never PUTs when positionMs is not finite", () => {
    const put = vi.fn();
    const reporter = makeReporter(put);

    reporter.report(Number.NaN, 60_000);
    reporter.report(Number.POSITIVE_INFINITY, 60_000);
    reporter.flush("pause");

    expect(put).not.toHaveBeenCalled();
    reporter.dispose();
  });

  it("dispose cancels the pending timer so no trailing PUT fires", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    await flushMicrotasks();
    reporter.report(2_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    reporter.dispose();
    vi.advanceTimersByTime(REPORT_INTERVAL_MS * 2);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("holds at most one in-flight PUT and coalesces unsent samples", async () => {
    const gates: ReturnType<typeof deferred>[] = [];
    const put = vi.fn(
      (_body: {
        file_id: string;
        media_kind: "movie" | "episode";
        position_ms: number;
        duration_ms: number;
      }) => {
        const gate = deferred();
        gates.push(gate);
        return gate.promise;
      },
    );
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);
    expect(gates).toHaveLength(1);

    reporter.report(2_000, 60_000);
    reporter.report(3_500, 60_000);
    vi.advanceTimersByTime(REPORT_INTERVAL_MS);
    await flushMicrotasks();
    // Still waiting on the first PUT — no concurrent second call.
    expect(put).toHaveBeenCalledTimes(1);

    gates[0]!.resolve();
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 3_500,
      duration_ms: 60_000,
    });

    gates[1]!.resolve();
    reporter.dispose();
  });

  it("preserves ordered PUT bodies even if prior promises resolve out of order", async () => {
    const gates: ReturnType<typeof deferred>[] = [];
    const put = vi.fn(
      (_body: {
        file_id: string;
        media_kind: "movie" | "episode";
        position_ms: number;
        duration_ms: number;
      }) => {
        const gate = deferred();
        gates.push(gate);
        return gate.promise;
      },
    );
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    reporter.report(10_000, 60_000);
    reporter.flush("pause");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    reporter.report(60_000, 60_000);
    reporter.flush("ended");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    // Resolve "later" work first if it were concurrent — serialization means
    // gate[1] does not exist yet, so reverse resolution cannot regress order.
    gates[0]!.resolve();
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls[1]![0]).toMatchObject({ position_ms: 60_000 });

    gates[1]!.resolve();
    await flushMicrotasks();
    // Terminal completed sample must not be followed by the superseded pause sample.
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.calls.map((c) => c[0]!.position_ms)).toEqual([1_000, 60_000]);

    reporter.dispose();
  });

  it("never replaces a queued terminal sample with a nonterminal one", async () => {
    const gates: ReturnType<typeof deferred>[] = [];
    const put = vi.fn(
      (_body: {
        file_id: string;
        media_kind: "movie" | "episode";
        position_ms: number;
        duration_ms: number;
      }) => {
        const gate = deferred();
        gates.push(gate);
        return gate.promise;
      },
    );
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    reporter.report(60_000, 60_000);
    reporter.flush("ended");
    await flushMicrotasks();

    reporter.report(20_000, 60_000);
    reporter.flush("seeked");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    gates[0]!.resolve();
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 60_000,
      duration_ms: 60_000,
    });

    gates[1]!.resolve();
    reporter.dispose();
  });

  it("continues draining after a failed PUT", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const gates: ReturnType<typeof deferred>[] = [];
    const put = vi.fn(
      (_body: {
        file_id: string;
        media_kind: "movie" | "episode";
        position_ms: number;
        duration_ms: number;
      }) => {
        const gate = deferred();
        gates.push(gate);
        return gate.promise;
      },
    );
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    reporter.report(4_000, 60_000);
    reporter.flush("pause");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    gates[0]!.reject(new Error("network down"));
    await flushMicrotasks();
    expect(warn).toHaveBeenCalled();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 4_000,
      duration_ms: 60_000,
    });

    gates[1]!.resolve();
    warn.mockRestore();
    reporter.dispose();
  });

  it("dispose does not cancel an already queued terminal flush", async () => {
    const gates: ReturnType<typeof deferred>[] = [];
    const put = vi.fn(
      (_body: {
        file_id: string;
        media_kind: "movie" | "episode";
        position_ms: number;
        duration_ms: number;
      }) => {
        const gate = deferred();
        gates.push(gate);
        return gate.promise;
      },
    );
    const reporter = makeReporter(put);

    reporter.report(1_000, 60_000);
    reporter.report(60_000, 60_000);
    reporter.flush("ended");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    reporter.dispose();
    gates[0]!.resolve();
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 60_000,
      duration_ms: 60_000,
    });

    gates[1]!.resolve();
  });

  it("clearProgress DELETEs and returns true on success", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const clear = vi.fn().mockResolvedValue({ error: undefined });
    const reporter = makeReporter(put, clear);

    await expect(reporter.clearProgress()).resolves.toBe(true);
    expect(clear).toHaveBeenCalledTimes(1);

    reporter.dispose();
  });

  it("clearProgress returns false when DELETE returns error and keeps lastSent", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const put = vi.fn().mockResolvedValue(undefined);
    const clear = vi.fn().mockResolvedValue({ error: { message: "boom" } });
    const reporter = makeReporter(put, clear);

    reporter.report(30_000, 60_000);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    await expect(reporter.clearProgress()).resolves.toBe(false);
    expect(warn).toHaveBeenCalled();

    // Failed clear must not reset lastSent — identical flush stays a no-op.
    reporter.flush("close");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    warn.mockRestore();
    reporter.dispose();
  });

  it("invokes onTerminalPut after a successful terminal flush, not on heartbeats", async () => {
    const onTerminalPut = vi.fn();
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put, async () => ({}), onTerminalPut);

    reporter.report(20_000, 60_000);
    await flushMicrotasks();
    expect(onTerminalPut).not.toHaveBeenCalled();

    reporter.report(60_000, 60_000);
    reporter.flush("ended");
    await flushMicrotasks();
    expect(onTerminalPut).toHaveBeenCalledTimes(1);

    reporter.dispose();
  });

  it("does not invoke onTerminalPut for non-terminal pause flushes", async () => {
    const onTerminalPut = vi.fn();
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put, async () => ({}), onTerminalPut);

    reporter.report(1_000, 60_000);
    await flushMicrotasks();
    reporter.report(4_000, 60_000);
    reporter.flush("pause");
    await flushMicrotasks();

    expect(put).toHaveBeenCalledTimes(2);
    expect(onTerminalPut).not.toHaveBeenCalled();
    reporter.dispose();
  });

  it("after successful clear, close under 5s reports from zero not the old lastSent", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const clear = vi.fn().mockResolvedValue({});
    const reporter = makeReporter(put, clear);

    reporter.report(30_000, 60_000);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    await expect(reporter.clearProgress()).resolves.toBe(true);

    // Start-over then close within the noise floor: must PUT the new small
    // sample rather than treating the deleted 30s row as lastSent.
    reporter.report(2_000, 60_000);
    reporter.flush("close");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 2_000,
      duration_ms: 60_000,
    });

    // Resume reporting above the noise floor works as a fresh window.
    reporter.report(6_000, 60_000);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(3);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 6_000,
      duration_ms: 60_000,
    });

    reporter.dispose();
  });

  it("failed non-terminal PUT is resent on the next flush of the same sample", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const put = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(5_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);
    await flushMicrotasks();

    reporter.report(5_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    reporter.flush();
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 5_000,
      duration_ms: 60_000,
    });

    warn.mockRestore();
    reporter.dispose();
  });

  it("failed terminal PUT skips onTerminalPut until a successful retry", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const onTerminalPut = vi.fn();
    const put = vi
      .fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue(undefined);
    const reporter = makeReporter(put, async () => ({}), onTerminalPut);

    reporter.report(60_000, 60_000);
    await flushMicrotasks();
    expect(onTerminalPut).not.toHaveBeenCalled();

    reporter.flush("ended");
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 60_000,
      duration_ms: 60_000,
    });
    expect(onTerminalPut).toHaveBeenCalledTimes(1);

    warn.mockRestore();
    reporter.dispose();
  });

  it("failed send does not clobber a newer pending sample", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const gate = deferred();
    const put = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(5_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    reporter.report(9_000, 60_000);
    gate.reject(new Error("network down"));
    await flushMicrotasks();

    vi.advanceTimersByTime(REPORT_INTERVAL_MS);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenLastCalledWith({
      file_id: fileId,
      media_kind: mediaKind,
      position_ms: 9_000,
      duration_ms: 60_000,
    });

    warn.mockRestore();
    reporter.dispose();
  });

  it("successful send still dedupes identical flush and fires onTerminalPut once", async () => {
    const onTerminalPut = vi.fn();
    const put = vi.fn().mockResolvedValue(undefined);
    const reporter = makeReporter(put, async () => ({}), onTerminalPut);

    reporter.report(5_000, 60_000);
    await flushMicrotasks();
    reporter.flush();
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    reporter.report(20_000, 60_000);
    await flushMicrotasks();
    reporter.report(60_000, 60_000);
    reporter.flush("ended");
    await flushMicrotasks();
    expect(onTerminalPut).toHaveBeenCalledTimes(1);

    reporter.dispose();
  });

  it("does not spontaneously retry a failed send from timers alone", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const gate = deferred();
    const put = vi.fn().mockReturnValueOnce(gate.promise).mockResolvedValue(undefined);
    const reporter = makeReporter(put);

    reporter.report(5_000, 60_000);
    expect(put).toHaveBeenCalledTimes(1);

    // Let the report window close while the PUT is still in flight so pending
    // is null at onWindowEnd. Failure then restores pending, but no timer remains.
    vi.advanceTimersByTime(REPORT_INTERVAL_MS);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    gate.reject(new Error("network down"));
    await flushMicrotasks();

    vi.advanceTimersByTime(REPORT_INTERVAL_MS * 3);
    await flushMicrotasks();
    expect(put).toHaveBeenCalledTimes(1);

    warn.mockRestore();
    reporter.dispose();
  });
});

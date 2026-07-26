import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createManagedEventSource } from "@/lib/managed-event-source";

type Listener = (ev: MessageEvent) => void;

/**
 * Minimal stand-in for the browser `EventSource`. Only the surface the
 * primitive touches is modelled: named-event listeners, `onmessage`,
 * `onerror`, `readyState` and `close()`.
 */
class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.OPEN;
  onmessage: Listener | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;
  listeners = new Map<string, Set<Listener>>();

  constructor(
    readonly url: string,
    readonly init?: { withCredentials?: boolean },
  ) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, handler: Listener) {
    const set = this.listeners.get(name) ?? new Set<Listener>();
    set.add(handler);
    this.listeners.set(name, set);
  }

  removeEventListener(name: string, handler: Listener) {
    this.listeners.get(name)?.delete(handler);
  }

  close() {
    this.closeCalls += 1;
    this.readyState = FakeEventSource.CLOSED;
  }

  /** Total live named-event listeners across every event name. */
  listenerCount() {
    let n = 0;
    for (const set of this.listeners.values()) n += set.size;
    return n;
  }

  emit(name: string, data?: unknown) {
    for (const handler of this.listeners.get(name) ?? []) {
      handler({ data: JSON.stringify(data ?? null) } as MessageEvent);
    }
  }

  /** Terminal failure: the browser gave up (readyState CLOSED). */
  failTerminally() {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.();
  }

  /** Transient failure: the browser is reconnecting. */
  failTransiently() {
    this.readyState = FakeEventSource.CONNECTING;
    this.onerror?.();
  }
}

const original = globalThis.EventSource;

beforeEach(() => {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
});

afterEach(() => {
  globalThis.EventSource = original;
  vi.useRealTimers();
});

function last(): FakeEventSource {
  const es = FakeEventSource.instances.at(-1);
  if (!es) throw new Error("no EventSource was constructed");
  return es;
}

describe("createManagedEventSource", () => {
  it("fires onDone exactly once on a normal server-side close", () => {
    const onDone = vi.fn();
    createManagedEventSource("/stream", { doneEvent: "done", onDone });

    last().emit("done");
    last().emit("done");

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith("completed");
    expect(last().closeCalls).toBe(1);
  });

  it("reports 'closed' when the browser gives up, but not on a transient error", () => {
    const onDone = vi.fn();
    createManagedEventSource("/stream", { doneEvent: "done", onDone });

    last().failTransiently();
    expect(onDone).not.toHaveBeenCalled();

    last().failTerminally();
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith("closed");
  });

  it("caps a stalled transient error with the timeout", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    createManagedEventSource("/stream", { doneEvent: "done", onDone, timeoutMs: 1000 });

    last().failTransiently();
    vi.advanceTimersByTime(1000);

    expect(onDone).toHaveBeenCalledExactlyOnceWith("timeout");
  });

  it("does not fire onAbort when close() follows a timeout-driven onDone", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    const onAbort = vi.fn();
    const handle = createManagedEventSource("/stream", {
      doneEvent: "done",
      onDone,
      onAbort,
      timeoutMs: 1000,
    });

    last().failTransiently();
    vi.advanceTimersByTime(1000);
    expect(onDone).toHaveBeenCalledExactlyOnceWith("timeout");

    // The timeout already settled the stream terminally; the caller's own
    // teardown (unmount / re-search) must not also report an abort.
    handle.close();

    expect(onAbort).not.toHaveBeenCalled();
    expect(last().closeCalls).toBe(1);
  });

  it("does not fire onDone on close() and tears the source down", () => {
    const onDone = vi.fn();
    const results = vi.fn();
    const handle = createManagedEventSource("/stream", {
      doneEvent: "done",
      events: { results },
      onDone,
      onMessage: vi.fn(),
    });
    expect(last().listenerCount()).toBe(2);

    handle.close();

    expect(onDone).not.toHaveBeenCalled();
    expect(last().closeCalls).toBe(1);
    expect(last().readyState).toBe(FakeEventSource.CLOSED);
    // Teardown detaches every listener, so nothing the caller passed stays
    // reachable from the source — and a late event reaches no handler.
    expect(last().listenerCount()).toBe(0);
    expect(last().onmessage).toBeNull();
    expect(last().onerror).toBeNull();
    last().emit("results", { hits: 1 });
    expect(results).not.toHaveBeenCalled();
  });

  it("is idempotent: a second close() is a no-op", () => {
    const onAbort = vi.fn();
    const handle = createManagedEventSource("/stream", { doneEvent: "done", onAbort });

    handle.close();
    handle.close();

    expect(onAbort).toHaveBeenCalledTimes(1);
    expect(last().closeCalls).toBe(1);
  });

  it("fires onAbort once on close(), and never onDone", () => {
    const onDone = vi.fn();
    const onAbort = vi.fn();
    const handle = createManagedEventSource("/stream", { doneEvent: "done", onDone, onAbort });

    handle.close();

    expect(onAbort).toHaveBeenCalledTimes(1);
    expect(onDone).not.toHaveBeenCalled();
  });

  it("fires onDone but never onAbort on natural completion", () => {
    const onDone = vi.fn();
    const onAbort = vi.fn();
    createManagedEventSource("/stream", { doneEvent: "done", onDone, onAbort });

    last().emit("done");

    expect(onDone).toHaveBeenCalledExactlyOnceWith("completed");
    expect(onAbort).not.toHaveBeenCalled();
  });

  it("fires neither callback when close() follows a natural completion", () => {
    const onDone = vi.fn();
    const onAbort = vi.fn();
    const handle = createManagedEventSource("/stream", { doneEvent: "done", onDone, onAbort });

    last().emit("done");
    onDone.mockClear();
    handle.close();

    expect(onDone).not.toHaveBeenCalled();
    expect(onAbort).not.toHaveBeenCalled();
    expect(last().closeCalls).toBe(1);
  });

  it("routes named events and plain messages to their handlers", () => {
    const results = vi.fn();
    const onMessage = vi.fn();
    createManagedEventSource("/stream", { events: { results }, onMessage });

    last().emit("results", { hits: 1 });
    last().onmessage?.({ data: "hi" } as MessageEvent);

    expect(results).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledTimes(1);
  });
});

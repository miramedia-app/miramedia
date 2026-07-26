import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createManagedEventSource, type ManagedEventSource } from "@/lib/managed-event-source";
import { createRunGenerationGuard } from "@/lib/run-generation";

describe("createRunGenerationGuard", () => {
  it("owns the token it just minted", () => {
    const guard = createRunGenerationGuard();
    const a = guard.begin();
    expect(guard.owns(a)).toBe(true);
  });

  it("invalidates every prior run when a new one begins", () => {
    const guard = createRunGenerationGuard();
    const a = guard.begin();
    const b = guard.begin();
    expect(guard.owns(a)).toBe(false);
    expect(guard.owns(b)).toBe(true);
  });

  it("abandon() invalidates the current run without minting one", () => {
    const guard = createRunGenerationGuard();
    const a = guard.begin();
    guard.abandon();
    expect(guard.owns(a)).toBe(false);
    // The next run is still ownable — abandon only burns a generation.
    const b = guard.begin();
    expect(guard.owns(b)).toBe(true);
  });
});

/**
 * Regression harness for the download dialog's search lifecycle.
 *
 * It mirrors `download-media-dialog.tsx` exactly: a promise per stream leg that
 * settles from either `onDone` or `onAbort`, and a `search()` that aborts the
 * previous legs before starting its own. Because `onAbort` resolves the
 * abandoned leg *synchronously* inside that abort loop, the abandoned
 * `search()` resumes in a microtask after the new one has already turned the
 * spinner on — so without the generation guard its tail reports partial results
 * and clears the running search's spinner. Both guarded side effects are
 * asserted below; removing either `owns()` check fails these tests.
 */
type Listener = (ev: MessageEvent) => void;

class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.OPEN;
  onmessage: Listener | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, Set<Listener>>();

  constructor(readonly url: string) {
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
    this.readyState = FakeEventSource.CLOSED;
  }
  emit(name: string, data?: unknown) {
    for (const handler of [...(this.listeners.get(name) ?? [])]) {
      handler({ data: JSON.stringify(data ?? null) } as MessageEvent);
    }
  }
}

const original = globalThis.EventSource;

beforeEach(() => {
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
});

afterEach(() => {
  globalThis.EventSource = original;
});

function makeDialog() {
  const guard = createRunGenerationGuard();
  const state = { isLoading: false, toasts: [] as string[], results: 0 };
  let handles: ManagedEventSource[] = [];

  function streamSearch(url: string, onChunk: (n: number) => void): Promise<void> {
    return new Promise<void>((resolve) => {
      const settle = () => {
        handles = handles.filter((x) => x !== handle);
        resolve();
      };
      const handle: ManagedEventSource = createManagedEventSource(url, {
        doneEvent: "done",
        events: {
          results: (ev) => onChunk((JSON.parse(ev.data) as { n: number }).n),
        },
        onDone: settle,
        onAbort: settle,
      });
      handles.push(handle);
    });
  }

  function abortStreams() {
    for (const h of handles) h.close();
    handles = [];
  }

  async function search(url: string) {
    const generation = guard.begin();
    abortStreams();
    state.isLoading = true;
    let found = 0;
    try {
      await streamSearch(url, (n) => {
        if (!guard.owns(generation)) return;
        found += n;
        state.results = found;
      });
      if (guard.owns(generation)) state.toasts.push(`Found ${found} torrents.`);
    } finally {
      if (guard.owns(generation)) state.isLoading = false;
    }
  }

  /** The `downloadTorrent` path: free the connection slot mid-search. */
  function download() {
    guard.abandon();
    abortStreams();
    state.isLoading = false;
  }

  return { state, search, download };
}

function source(index: number): FakeEventSource {
  const es = FakeEventSource.instances[index];
  if (!es) throw new Error(`no EventSource at ${index}`);
  return es;
}

describe("download dialog search lifecycle", () => {
  it("does not let a restarted search's tail toast or clear the new spinner", async () => {
    const dialog = makeDialog();

    const first = dialog.search("/stream/1");
    source(0).emit("results", { n: 3 });

    // Re-running the search aborts leg 1, which settles its promise.
    const second = dialog.search("/stream/2");
    await first;

    expect(dialog.state.toasts).toEqual([]);
    expect(dialog.state.isLoading).toBe(true);

    // Leg 1's chunks must not bleed into the new run's result count either.
    source(0).emit("results", { n: 9 });
    source(1).emit("results", { n: 5 });
    source(1).emit("done");
    await second;

    expect(dialog.state.toasts).toEqual(["Found 5 torrents."]);
    expect(dialog.state.results).toBe(5);
    expect(dialog.state.isLoading).toBe(false);
  });

  it("clears the spinner exactly once when a search completes normally", async () => {
    const dialog = makeDialog();
    const run = dialog.search("/stream/1");
    expect(dialog.state.isLoading).toBe(true);

    source(0).emit("results", { n: 2 });
    source(0).emit("done");
    await run;

    expect(dialog.state.toasts).toEqual(["Found 2 torrents."]);
    expect(dialog.state.isLoading).toBe(false);
  });

  it("settles the search on a download-initiated abort without a partial toast", async () => {
    const dialog = makeDialog();
    const run = dialog.search("/stream/1");
    source(0).emit("results", { n: 4 });

    dialog.download();
    await run;

    expect(dialog.state.toasts).toEqual([]);
    expect(dialog.state.isLoading).toBe(false);
  });
});

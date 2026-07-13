/**
 * One primitive for the browser EventSource dance that several call sites were
 * each re-deriving:
 *
 *  - `onerror` fires on *transient* reconnects too (readyState CONNECTING), not
 *    only terminal failures. Terminate immediately only when the browser has
 *    given up (readyState CLOSED); otherwise let it auto-reconnect and cap the
 *    stalled-error state with a timer so a truly-dead server can't hang forever.
 *  - `onDone` is invoked at most once, with the terminal outcome.
 *  - `close()` is idempotent and tears down without firing `onDone` (a
 *    user-/caller-initiated abort is not a terminal stream outcome).
 *
 * Named SSE events (`addEventListener("results", …)`) are supported via the
 * `events` map; plain `message` events via `onMessage`. A normal end-of-stream
 * marker event is named via `doneEvent` and reported as `"completed"`.
 *
 * NOTE ON TIMER + MESSAGES (intentional): once the error timer is armed by a
 * transient error, an incoming message does NOT clear it. This mirrors the
 * exact pre-extraction behavior of every call site (none reset the cap on
 * resumed messages) so this consolidation changes no observable behavior. If a
 * future change wants "clear the cap when messages resume", do it deliberately
 * here — it is a behavior change, not a refactor.
 */

export type ManagedEventSourceOutcome = "completed" | "error" | "timeout" | "closed";

export interface ManagedEventSourceOptions {
  /** Named SSE events → handler (`addEventListener` per key). */
  events?: Record<string, (ev: MessageEvent) => void>;
  /** Handler for unnamed `message` events. */
  onMessage?: (ev: MessageEvent) => void;
  /** Name of the event that marks a normal end of stream (→ `"completed"`). */
  doneEvent?: string;
  /** Invoked at most once with the terminal outcome. Not called on `close()`. */
  onDone?: (outcome: ManagedEventSourceOutcome) => void;
  /** Cap (ms) on the stalled-error state before reporting `"timeout"`. */
  timeoutMs?: number;
  withCredentials?: boolean;
}

export interface ManagedEventSource {
  /** Idempotent teardown. Does not fire `onDone`. */
  close: () => void;
}

export function createManagedEventSource(
  url: string,
  opts: ManagedEventSourceOptions,
): ManagedEventSource {
  const { events, onMessage, doneEvent, onDone, timeoutMs, withCredentials } = opts;

  const es = new EventSource(url, { withCredentials: withCredentials ?? false });

  let done = false;
  let errorTimer: ReturnType<typeof setTimeout> | null = null;

  const clearErrorTimer = () => {
    if (errorTimer) {
      clearTimeout(errorTimer);
      errorTimer = null;
    }
  };

  const teardown = () => {
    clearErrorTimer();
    es.close();
  };

  // Terminal completion path: fire `onDone` exactly once.
  const complete = (outcome: ManagedEventSourceOutcome) => {
    if (done) return;
    done = true;
    teardown();
    onDone?.(outcome);
  };

  if (onMessage) es.onmessage = onMessage;

  if (events) {
    for (const [name, handler] of Object.entries(events)) {
      es.addEventListener(name, handler as EventListener);
    }
  }

  if (doneEvent) {
    es.addEventListener(doneEvent, () => complete("completed"));
  }

  es.onerror = () => {
    if (done) return;
    if (es.readyState === EventSource.CLOSED) {
      complete("closed");
    } else if (timeoutMs != null && !errorTimer) {
      // Transient (CONNECTING) reconnect — arm the cap once, keep it running.
      errorTimer = setTimeout(() => complete("timeout"), timeoutMs);
    }
  };

  return {
    close: () => {
      if (done) return;
      // Mark terminal so a later `done`/`onerror` can't fire `onDone` after a
      // caller-initiated abort.
      done = true;
      teardown();
    },
  };
}

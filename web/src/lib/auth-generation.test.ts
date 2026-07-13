import { describe, expect, it, vi } from "vitest";

import { createAuthCoordinator, createAuthTransitionStore } from "@/lib/auth-generation";

/** A handler whose completion the test controls. */
function deferred() {
  let resolve!: () => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("createAuthCoordinator", () => {
  it("runs one handler for a 401 and retires the flight", async () => {
    const c = createAuthCoordinator();
    const gate = deferred();
    const handler = vi.fn(() => gate.promise);
    c.setUnauthorizedHandler(handler);

    const flight = c.reportUnauthorized(c.current());
    expect(c.isExiting()).toBe(true);
    gate.resolve();
    await flight;

    expect(handler).toHaveBeenCalledTimes(1);
    expect(c.isExiting()).toBe(false);
  });

  it("joins concurrent reports into the exact same flight, running one handler", async () => {
    const c = createAuthCoordinator();
    const gate = deferred();
    const handler = vi.fn(() => gate.promise);
    c.setUnauthorizedHandler(handler);

    const token = c.current();
    const a = c.reportUnauthorized(token);
    const b = c.reportUnauthorized(token);
    // A later, stale token arriving mid-flight must join too, not be dropped.
    const cFlight = c.reportUnauthorized(token - 1);
    expect(b).toBe(a);
    expect(cFlight).toBe(a);

    gate.resolve();
    await a;
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("turns a synchronously throwing handler into a rejected flight, not an escaping throw", async () => {
    const c = createAuthCoordinator();
    c.setUnauthorizedHandler(() => {
      throw new Error("boom");
    });

    // The throw must not escape reportUnauthorized: it has already advanced the
    // generation, so an escape would leave no active flight behind it.
    const flight = c.reportUnauthorized(c.current());
    expect(c.isExiting()).toBe(true);
    await expect(flight).rejects.toThrow("boom");
    expect(c.isExiting()).toBe(false);
  });

  it("lets a reentrant report from inside the handler join the same flight", async () => {
    const c = createAuthCoordinator();
    const gate = deferred();
    let inner: Promise<void> | undefined;
    const handler = vi.fn((token: number) => {
      // A second 401 landing while the handler runs must not open a second exit.
      inner = c.reportUnauthorized(token);
      return gate.promise;
    });
    c.setUnauthorizedHandler(handler);

    const outer = c.reportUnauthorized(c.current());
    gate.resolve();
    await outer;

    expect(handler).toHaveBeenCalledTimes(1);
    expect(inner).toBe(outer);
  });

  it("drops a stale 401 with no flight active — it cannot open an exit", async () => {
    const c = createAuthCoordinator();
    const handler = vi.fn();
    c.setUnauthorizedHandler(handler);

    const stale = c.current();
    await c.beginTransition(); // new session; `stale` now speaks for the old one

    await c.reportUnauthorized(stale);
    expect(handler).not.toHaveBeenCalled();
    expect(c.isExiting()).toBe(false);
  });

  it("lets the current generation open a new exit after the previous flight retired", async () => {
    const c = createAuthCoordinator();
    const handler = vi.fn();
    c.setUnauthorizedHandler(handler);

    await c.reportUnauthorized(c.current());
    expect(c.isExiting()).toBe(false);
    await c.reportUnauthorized(c.current());

    expect(handler).toHaveBeenCalledTimes(2);
  });

  it("beginTransition advances the generation before it drains the exit", async () => {
    const c = createAuthCoordinator();
    const gate = deferred();
    c.setUnauthorizedHandler(() => gate.promise);

    const before = c.current();
    const exit = c.reportUnauthorized(before);

    let settled = false;
    const transition = c.beginTransition().then((t) => {
      settled = true;
      return t;
    });

    // Advanced synchronously: a response still in flight is already stale and
    // cannot open an exit against the session we are entering.
    expect(c.isCurrent(before)).toBe(false);
    await Promise.resolve();
    expect(settled).toBe(false); // still draining the exit flight

    gate.resolve();
    await exit;
    const token = await transition;
    expect(settled).toBe(true);
    expect(c.isCurrent(token)).toBe(true);
    expect(c.isExiting()).toBe(false);
  });

  it("beginTransition drains a rejecting exit rather than propagating it", async () => {
    const c = createAuthCoordinator();
    const gate = deferred();
    c.setUnauthorizedHandler(() => gate.promise);
    const exit = c.reportUnauthorized(c.current());
    exit.catch(() => {});

    const transition = c.beginTransition();
    gate.reject(new Error("navigation failed"));
    await expect(transition).resolves.toBeTypeOf("number");
  });
});

describe("createAuthTransitionStore", () => {
  it("notifies subscribers on begin and is idempotent", () => {
    const store = createAuthTransitionStore();
    const listener = vi.fn();
    store.subscribe(listener);

    expect(store.isTransitioning()).toBe(false);
    store.begin();
    expect(store.isTransitioning()).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);

    store.begin();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("stops notifying after unsubscribe", () => {
    const store = createAuthTransitionStore();
    const listener = vi.fn();
    store.subscribe(listener)();
    store.begin();
    expect(listener).not.toHaveBeenCalled();
  });

  it("never leaves the blank state — nothing resets it", () => {
    const store = createAuthTransitionStore();
    store.begin();
    expect(store.isTransitioning()).toBe(true);
    expect(Object.keys(store)).not.toContain("end");
    expect(Object.keys(store)).not.toContain("reset");
  });
});

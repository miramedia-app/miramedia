import { afterEach, describe, expect, it, vi } from "vitest";

import { beginAuthTransition, hardNavigate, resetAuthCache } from "@/lib/auth";
import { authCoordinator, authTransition } from "@/lib/auth-generation";

type LocationStub = {
  replace: (p: string) => void;
  assign: (p: string) => void;
  href: string;
};

/**
 * `hardNavigate` only ever touches `window.location`, so a plain object is a
 * faithful stand-in — no DOM environment needed.
 */
function stubWindow(location: LocationStub) {
  (globalThis as { window?: unknown }).window = { location };
}

const THROWS = () => {
  throw new Error("blocked");
};

afterEach(() => {
  delete (globalThis as { window?: unknown }).window;
});

describe("hardNavigate", () => {
  it("uses location.replace first — the dead session must not be reachable via Back", () => {
    const replace = vi.fn();
    const assign = vi.fn();
    stubWindow({ replace, assign, href: "" });

    expect(hardNavigate("/login")).toBe(true);
    expect(replace).toHaveBeenCalledWith("/login");
    expect(assign).not.toHaveBeenCalled();
  });

  it("falls back to assign, then href, when the earlier mechanism throws", () => {
    const assign = vi.fn();
    const loc: LocationStub = { replace: THROWS, assign, href: "" };
    stubWindow(loc);
    expect(hardNavigate("/login")).toBe(true);
    expect(assign).toHaveBeenCalledWith("/login");

    const hrefOnly: LocationStub = { replace: THROWS, assign: THROWS, href: "" };
    stubWindow(hrefOnly);
    expect(hardNavigate("https://idp.example/authorize")).toBe(true);
    expect(hrefOnly.href).toBe("https://idp.example/authorize");
  });

  it("reports failure and never falls back to an SPA navigation", () => {
    const loc: LocationStub = {
      replace: THROWS,
      assign: THROWS,
      get href() {
        return "";
      },
      set href(_value: string) {
        THROWS();
      },
    };
    stubWindow(loc);

    // All three full-document mechanisms failed: stay blank rather than keep the
    // dead session's observers alive behind a client-side route change.
    expect(hardNavigate("/login")).toBe(false);
  });
});

describe("resetAuthCache", () => {
  it("cancels in flight queries before clearing the cache", async () => {
    const order: string[] = [];
    const qc = {
      cancelQueries: vi.fn(async () => {
        order.push("cancel");
      }),
      clear: vi.fn(() => {
        order.push("clear");
      }),
    };

    await resetAuthCache(qc as never);
    expect(order).toEqual(["cancel", "clear"]);
  });

  it("still clears when cancellation fails", async () => {
    const clear = vi.fn();
    const qc = {
      cancelQueries: vi.fn(async () => {
        throw new Error("cancel failed");
      }),
      clear,
    };

    // Cancellation is best-effort transport cleanup; it must never keep the
    // previous account's cached identity warm.
    await expect(resetAuthCache(qc as never)).rejects.toThrow("cancel failed");
    expect(clear).toHaveBeenCalledTimes(1);
  });
});

// Runs last: `authTransition` is a module singleton whose blank state is
// permanent by design, so this assertion cannot be undone for other tests.
describe("beginAuthTransition", () => {
  it("blanks the tree and advances the generation before it clears the cache", async () => {
    const seen: { transitioning: boolean; generationAdvanced: boolean }[] = [];
    const before = authCoordinator.current();
    const qc = {
      cancelQueries: vi.fn(async () => {
        seen.push({
          transitioning: authTransition.isTransitioning(),
          generationAdvanced: !authCoordinator.isCurrent(before),
        });
      }),
      clear: vi.fn(),
    };

    expect(authTransition.isTransitioning()).toBe(false);
    const token = await beginAuthTransition(qc as never);

    // By the time the cache is touched, no mounted observer can still report the
    // old identity and no in-flight response can open an exit against the new one.
    expect(seen).toEqual([{ transitioning: true, generationAdvanced: true }]);
    expect(qc.clear).toHaveBeenCalledTimes(1);
    expect(authCoordinator.isCurrent(token)).toBe(true);
    expect(authTransition.isTransitioning()).toBe(true);
  });
});

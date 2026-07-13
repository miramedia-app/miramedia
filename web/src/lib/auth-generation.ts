/**
 * Auth-generation coordinator + auth-transition state.
 *
 * The SPA keeps one QueryClient and one cookie jar across logins, so auth exits
 * (401 / session expiry) race with auth entries (login / OAuth). Failure modes:
 *
 *  - A burst of concurrent requests all 401 and each one clears + redirects.
 *  - A slow request from the *previous* account 401s only after the next account
 *    logged in, then clears the new user's cache and bounces them to /login.
 *
 * Rules, kept deliberately mechanical:
 *
 *  1. While ANY exit flight is active, `reportUnauthorized` joins that exact
 *     flight — no second handler, no further generation advance, whatever token
 *     it carries. One exit at a time, always.
 *  2. The active flight is stored as the same wrapped promise it hands out, and
 *     retired only if that identity still matches.
 *  3. An entry transition advances the generation first (so responses in flight
 *     are already stale and cannot affect the credential POST that follows) and
 *     then drains every prior exit flight before it completes.
 *
 * No React, no network: `createAuthCoordinator` is deterministic and unit-testable
 * by driving `reportUnauthorized` / `beginTransition` against a deferred handler.
 */

/** Opaque generation stamp. An exit may only redirect while its token is live. */
export type AuthGeneration = number;

export type UnauthorizedHandler = (token: AuthGeneration) => Promise<void> | void;

export type AuthCoordinator = {
  current(): AuthGeneration;
  isCurrent(token: AuthGeneration): boolean;
  setUnauthorizedHandler(fn: UnauthorizedHandler): void;
  /** True while an exit handler is in flight. */
  isExiting(): boolean;
  /**
   * Report a 401 for a request tagged `token`. Returns the exit flight to await.
   * Joins the active flight if there is one; otherwise only a current token may
   * open a new one.
   */
  reportUnauthorized(token: AuthGeneration): Promise<void>;
  /**
   * Begin an auth entry (login / logout / OAuth). Advances the generation, then
   * resolves once all prior exit work has settled.
   */
  beginTransition(): Promise<AuthGeneration>;
};

export function createAuthCoordinator(): AuthCoordinator {
  let generation: AuthGeneration = 0;
  let handler: UnauthorizedHandler = () => {};
  let exitFlight: Promise<void> | null = null;

  return {
    current: () => generation,
    isCurrent: (token) => token === generation,
    isExiting: () => exitFlight !== null,
    setUnauthorizedHandler(fn) {
      handler = fn;
    },
    reportUnauthorized(token) {
      // Rule 1: one exit at a time. Anything arriving while an exit is running
      // joins it — it must not advance the generation or run a second handler.
      if (exitFlight) return exitFlight;
      // No exit active: only a 401 that still speaks for the live session may
      // open one. A stale token here answers a request from a session we have
      // already left, so it is dropped.
      if (token !== generation) return Promise.resolve();
      // Advance synchronously, before any await, so peers racing us in this tick
      // see an active flight (or a stale token) and take the branches above.
      const exitToken = ++generation;
      const flight: Promise<void> = Promise.resolve(handler(exitToken))
        .then(() => {})
        .finally(() => {
          // Rule 2: retire only if this exact wrapped promise is still the
          // active one.
          if (exitFlight === flight) exitFlight = null;
        });
      exitFlight = flight;
      return flight;
    },
    async beginTransition() {
      // Rule 3: advance first — any response still in flight is now stale and
      // cannot open an exit against the session we are about to establish.
      const token = ++generation;
      // Then drain. Loop: a flight that was already pending when we advanced may
      // still be replaced by its own `finally`, and we must not return early.
      while (exitFlight) {
        const pending = exitFlight;
        await pending.catch(() => {});
        if (exitFlight === pending) break;
      }
      return token;
    },
  };
}

/**
 * Auth-transition state, subscribed by UserProvider and the dashboard auth gate.
 *
 * `QueryClient.clear()` does not blank the *active* QueryObserver results already
 * held by mounted components: if navigation throws or stalls, UserProvider would
 * keep exposing the old `is_superuser: true` and the privileged integrity UI would
 * stay painted. So a transition flips this flag BEFORE any cancel/clear/navigate,
 * and nothing resets it — the tree stays blank until a new document initializes.
 */
type Listener = () => void;

export function createAuthTransitionStore() {
  let transitioning = false;
  const listeners = new Set<Listener>();
  return {
    /** Enter the blanked state. Never automatically undone; a new document ends it. */
    begin() {
      if (transitioning) return;
      transitioning = true;
      for (const l of listeners) l();
    },
    isTransitioning: () => transitioning,
    subscribe(l: Listener) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}

export type AuthTransitionStore = ReturnType<typeof createAuthTransitionStore>;

/**
 * Shared instances, pinned to `globalThis` so HMR-driven module re-evaluation in
 * dev doesn't swap in a fresh coordinator (resetting the handler to a noop and
 * the generation to 0) while requests tagged by the old one are still in flight.
 */
const COORDINATOR_KEY = Symbol.for("mm.auth.coordinator");
const TRANSITION_KEY = Symbol.for("mm.auth.transition");
type Holder = {
  [COORDINATOR_KEY]?: AuthCoordinator;
  [TRANSITION_KEY]?: AuthTransitionStore;
};
const holder = globalThis as unknown as Holder;
holder[COORDINATOR_KEY] ??= createAuthCoordinator();
holder[TRANSITION_KEY] ??= createAuthTransitionStore();

export const authCoordinator: AuthCoordinator = holder[COORDINATOR_KEY];
export const authTransition: AuthTransitionStore = holder[TRANSITION_KEY];

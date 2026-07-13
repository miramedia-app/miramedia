/**
 * Auth-generation coordinator.
 *
 * The SPA keeps one QueryClient and one cookie jar across logins, so auth exits
 * (401 / session expiry) race with auth entries (login). Two failure modes:
 *
 *  - A burst of concurrent requests all 401 at once and each one clears the
 *    cache and pushes `/login`.
 *  - A slow request issued by the *previous* account 401s only after the next
 *    account has already logged in — and its handler then clears the new user's
 *    cache and bounces them to `/login`.
 *
 * Both are fixed by generations. Every request is tagged with the generation it
 * was sent under. A 401 may only start an exit if its tag is still current; the
 * first such 401 synchronously advances the generation (invalidating every
 * in-flight peer) and installs a single shared exit promise that later arrivals
 * await instead of duplicating. Any auth entry (login, logout, OAuth) likewise
 * advances the generation and awaits the older exit flight, so stale work can
 * never finish on top of a fresh session.
 *
 * This module holds no React or network dependency: `createAuthCoordinator` is a
 * deterministic factory, unit-testable by driving `beginTransition` /
 * `reportUnauthorized` by hand.
 */

/** Opaque generation stamp. An exit is only allowed to redirect while current. */
export type AuthGeneration = number;

export type UnauthorizedHandler = (token: AuthGeneration) => Promise<void> | void;

export type AuthCoordinator = {
  /** The generation new requests should be tagged with. */
  current(): AuthGeneration;
  /** True while `token` is still the live generation (no newer transition won). */
  isCurrent(token: AuthGeneration): boolean;
  /** Install the handler that performs the actual cache reset + redirect. */
  setUnauthorizedHandler(fn: UnauthorizedHandler): void;
  /**
   * Report a 401 for a request tagged `token`. Returns the exit flight to await.
   * Stale tokens never start an exit; they join the flight already in progress
   * (or resolve immediately if it has finished).
   */
  reportUnauthorized(token: AuthGeneration): Promise<void>;
  /**
   * Begin an auth entry (login / logout / OAuth). Advances the generation so any
   * in-flight exit is invalidated, then resolves once older exit work has
   * settled — callers reset the cache and navigate only after awaiting this.
   */
  beginTransition(): Promise<AuthGeneration>;
};

export function createAuthCoordinator(): AuthCoordinator {
  let generation: AuthGeneration = 0;
  let handler: UnauthorizedHandler = () => {};
  let exitFlight: Promise<void> | null = null;

  function settleExit(promise: Promise<void>): Promise<void> {
    exitFlight = promise.finally(() => {
      // Only retire the flight if no newer one replaced it.
      if (exitFlight === promise) exitFlight = null;
    });
    return exitFlight;
  }

  return {
    current: () => generation,
    isCurrent: (token) => token === generation,
    setUnauthorizedHandler(fn) {
      handler = fn;
    },
    reportUnauthorized(token) {
      // Stale 401: the session already moved on (a peer 401 won, or the user has
      // since logged in). Never clear or redirect on its behalf.
      if (token !== generation) return exitFlight ?? Promise.resolve();
      // Advance synchronously, before any await, so peers racing us in the same
      // tick see a stale token and fall into the branch above.
      const exitToken = ++generation;
      return settleExit(Promise.resolve(handler(exitToken)).then(() => {}));
    },
    async beginTransition() {
      const previous = exitFlight;
      // Invalidate any exit in progress: its token is no longer current, so its
      // redirect is skipped even if it is only part-way through its cache reset.
      const token = ++generation;
      // Let that older exit finish its work before we reset and navigate,
      // otherwise it could clear the cache we are about to populate.
      if (previous) await previous.catch(() => {});
      return token;
    },
  };
}

/**
 * The coordinator the API middleware and the auth screens share.
 *
 * Pinned to `globalThis` so HMR-driven module re-evaluation in dev doesn't swap
 * in a fresh coordinator (resetting the handler to a noop and the generation to
 * 0) while requests tagged by the old one are still in flight.
 */
const COORDINATOR_KEY = Symbol.for("mm.auth.coordinator");
type CoordinatorHolder = { [COORDINATOR_KEY]?: AuthCoordinator };
const holder = globalThis as unknown as CoordinatorHolder;
holder[COORDINATOR_KEY] ??= createAuthCoordinator();

export const authCoordinator: AuthCoordinator = holder[COORDINATOR_KEY];

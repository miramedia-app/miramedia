/**
 * Generation (epoch) token for a restartable async run.
 *
 * A run that can be restarted or abandoned mid-flight leaves its continuation
 * scheduled: the awaited work settles later, in a microtask, and then keeps
 * writing state that belongs to whatever run is current *now*. The classic
 * damage is a stale `finally { setIsLoading(false) }` killing the spinner of
 * the run that replaced it, plus a completion toast reporting the stale run's
 * partial results.
 *
 * `begin()` stamps a run and invalidates every older one; `owns(token)` is the
 * check each side effect makes before it fires. Deliberately a counter and two
 * methods — no state machine, no subscriptions.
 */
export type RunGeneration = number;

export type RunGenerationGuard = {
  /** Stamp a new run, invalidating all prior ones. */
  begin: () => RunGeneration;
  /** True while `token` is still the newest run. */
  owns: (token: RunGeneration) => boolean;
  /**
   * Invalidate the current run without starting one (e.g. tearing streams down
   * to free a connection slot). Its continuation then owns nothing and no-ops.
   */
  abandon: () => void;
};

export function createRunGenerationGuard(): RunGenerationGuard {
  let current: RunGeneration = 0;
  return {
    begin: () => ++current,
    owns: (token) => token === current,
    abandon: () => {
      current += 1;
    },
  };
}

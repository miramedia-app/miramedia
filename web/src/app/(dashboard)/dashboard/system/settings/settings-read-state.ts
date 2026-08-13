/**
 * Settings page load semantics for the dual settings+schema queries.
 *
 * Terminal failures must surface as `error` (retryable), not as perpetual
 * pending. Readiness is derived from query status — never from coercing missing
 * `settings.misc` into an empty editable form.
 */

export type SettingsReadView = "pending" | "error" | "ready";

export function settingsReadViewState(args: {
  settingsIsPending: boolean;
  settingsIsError: boolean;
  schemaIsPending: boolean;
  schemaIsError: boolean;
}): SettingsReadView {
  if (args.settingsIsError || args.schemaIsError) return "error";
  if (args.settingsIsPending || args.schemaIsPending) return "pending";
  return "ready";
}

/** Legacy readiness used `!!settings.misc`, which stays false after a failed read. */
export function legacySettingsLoadedFromMisc(settings: { misc?: unknown }): boolean {
  return !!settings.misc;
}

/**
 * Refetch failed queries. When both failed (or the caller has no error flags),
 * refetch both — matching "retry the failed query or both".
 */
export function retrySettingsReads(args: {
  settingsIsError: boolean;
  schemaIsError: boolean;
  refetchSettings: () => unknown;
  refetchSchema: () => unknown;
}): void {
  const both =
    (args.settingsIsError && args.schemaIsError) || (!args.settingsIsError && !args.schemaIsError);
  if (both || args.settingsIsError) void args.refetchSettings();
  if (both || args.schemaIsError) void args.refetchSchema();
}

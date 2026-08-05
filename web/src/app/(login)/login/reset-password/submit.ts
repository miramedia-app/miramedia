/**
 * Async state machine for the password-reset submit path.
 *
 * Extracted from the page component so its completion/loading-reset behavior can
 * be unit-tested in Node without a DOM. The invariant this guards: `setLoading`
 * must be cleared for every terminal outcome — success, resolved HTTP failure,
 * and a rejected transport (offline/proxy) — so a rejected attempt can be
 * retried without reloading a credential-bearing page.
 *
 * No reset credential is ever passed to `onTransportError` or logged here; the
 * caller supplies a generic, credential-free connectivity message.
 */
export type ResetPasswordSubmitDeps = {
  /** Fires the reset request; resolves with the transport response. */
  request: () => Promise<{ response: { ok: boolean } }>;
  /** Toggles the busy/loading state. */
  setLoading: (value: boolean) => void;
  /** Resolved 2xx: show success and navigate. */
  onSuccess: () => void;
  /** Resolved non-2xx: show a generic failure message. */
  onHttpFailure: () => void;
  /** Rejected transport: show a generic connectivity message (no credential). */
  onTransportError: () => void;
};

export async function submitPasswordReset(deps: ResetPasswordSubmitDeps): Promise<void> {
  deps.setLoading(true);
  try {
    const { response } = await deps.request();
    if (response.ok) {
      deps.onSuccess();
    } else {
      deps.onHttpFailure();
    }
  } catch {
    // Deliberately swallow the exception value: it must never reach the UI or
    // logs, since it could carry request context. Show a generic message.
    deps.onTransportError();
  } finally {
    deps.setLoading(false);
  }
}

import { describe, expect, it, vi } from "vitest";

import { submitPasswordReset, type ResetPasswordSubmitDeps } from "./submit";

/** Build deps with spy callbacks and a caller-controlled request. */
function makeDeps(request: ResetPasswordSubmitDeps["request"]): {
  deps: ResetPasswordSubmitDeps;
  loadingCalls: boolean[];
} {
  const loadingCalls: boolean[] = [];
  const deps: ResetPasswordSubmitDeps = {
    request,
    setLoading: (v) => loadingCalls.push(v),
    onSuccess: vi.fn(),
    onHttpFailure: vi.fn(),
    onTransportError: vi.fn(),
  };
  return { deps, loadingCalls };
}

describe("submitPasswordReset", () => {
  it("resolved 2xx: navigates, clears loading, no failure paths", async () => {
    const { deps, loadingCalls } = makeDeps(async () => ({ response: { ok: true } }));

    await submitPasswordReset(deps);

    expect(deps.onSuccess).toHaveBeenCalledTimes(1);
    expect(deps.onHttpFailure).not.toHaveBeenCalled();
    expect(deps.onTransportError).not.toHaveBeenCalled();
    expect(loadingCalls).toEqual([true, false]);
  });

  it("resolved non-2xx: shows HTTP failure and clears loading", async () => {
    const { deps, loadingCalls } = makeDeps(async () => ({ response: { ok: false } }));

    await submitPasswordReset(deps);

    expect(deps.onHttpFailure).toHaveBeenCalledTimes(1);
    expect(deps.onSuccess).not.toHaveBeenCalled();
    expect(deps.onTransportError).not.toHaveBeenCalled();
    expect(loadingCalls).toEqual([true, false]);
  });

  it("rejected transport: shows generic error and clears loading", async () => {
    const { deps, loadingCalls } = makeDeps(async () => {
      throw new Error("network down");
    });

    await submitPasswordReset(deps);

    expect(deps.onTransportError).toHaveBeenCalledTimes(1);
    // The generic handler receives no arguments — no exception text or credential.
    expect((deps.onTransportError as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([]);
    expect(deps.onSuccess).not.toHaveBeenCalled();
    expect(deps.onHttpFailure).not.toHaveBeenCalled();
    expect(loadingCalls).toEqual([true, false]);
  });

  it("rejection is retryable: a second submit reaches the request again", async () => {
    const request = vi
      .fn<ResetPasswordSubmitDeps["request"]>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ response: { ok: true } });
    const { deps, loadingCalls } = makeDeps(request);

    await submitPasswordReset(deps);
    // Loading was cleared after the rejection, so the button is enabled again.
    expect(loadingCalls).toEqual([true, false]);
    expect(deps.onTransportError).toHaveBeenCalledTimes(1);

    await submitPasswordReset(deps);
    expect(request).toHaveBeenCalledTimes(2);
    expect(deps.onSuccess).toHaveBeenCalledTimes(1);
    expect(loadingCalls).toEqual([true, false, true, false]);
  });
});

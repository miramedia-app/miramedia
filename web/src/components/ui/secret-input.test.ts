import { describe, expect, it } from "vitest";

import { SECRET_MASK, resolveSecretInputState } from "@/lib/secret-mask";

describe("SecretInput masked value semantics", () => {
  it("renders masked server values as empty password fields with no reveal control", () => {
    const state = resolveSecretInputState(SECRET_MASK, false);
    expect(state.displayValue).toBe("");
    expect(state.showReveal).toBe(false);
    expect(state.inputType).toBe("password");

    const revealedAttempt = resolveSecretInputState(SECRET_MASK, true);
    expect(revealedAttempt.displayValue).toBe("");
    expect(revealedAttempt.showReveal).toBe(false);
    expect(revealedAttempt.inputType).toBe("password");
  });

  it("passes through newly typed values for display and reveal", () => {
    const typed = "operator-entered-secret";
    const hidden = resolveSecretInputState(typed, false);
    expect(hidden.displayValue).toBe(typed);
    expect(hidden.showReveal).toBe(true);
    expect(hidden.inputType).toBe("password");

    const shown = resolveSecretInputState(typed, true);
    expect(shown.displayValue).toBe(typed);
    expect(shown.showReveal).toBe(true);
    expect(shown.inputType).toBe("text");
  });
});

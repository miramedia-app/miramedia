import { describe, expect, it } from "vitest";

import {
  SECRET_MASK,
  canRevealSecretValue,
  isMaskedSecretValue,
  secretInputDisplayValue,
} from "@/lib/secret-mask";

describe("secret mask helpers", () => {
  it("treats only the exact sentinel as masked", () => {
    expect(isMaskedSecretValue(SECRET_MASK)).toBe(true);
    expect(isMaskedSecretValue("*********")).toBe(false);
    expect(isMaskedSecretValue("")).toBe(false);
    expect(isMaskedSecretValue("real-secret")).toBe(false);
  });

  it("renders masked values as empty for display", () => {
    expect(secretInputDisplayValue(SECRET_MASK)).toBe("");
    expect(secretInputDisplayValue("new-value")).toBe("new-value");
    expect(secretInputDisplayValue(null)).toBe("");
  });

  it("disallows reveal for masked or empty values", () => {
    expect(canRevealSecretValue(SECRET_MASK)).toBe(false);
    expect(canRevealSecretValue("")).toBe(false);
    expect(canRevealSecretValue(null)).toBe(false);
    expect(canRevealSecretValue("typed-secret")).toBe(true);
  });
});

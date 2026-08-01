/** Must match ``SECRET_MASK`` in ``miramedia/settings/validation.py``. */
export const SECRET_MASK = "********";

export function isMaskedSecretValue(value: string | null | undefined): boolean {
  return value === SECRET_MASK;
}

/** Empty string when the server sent the unchanged sentinel. */
export function secretInputDisplayValue(value: string | null | undefined): string {
  return isMaskedSecretValue(value) ? "" : (value ?? "");
}

export function canRevealSecretValue(value: string | null | undefined): boolean {
  return !isMaskedSecretValue(value) && Boolean(value);
}

export function resolveSecretInputState(
  value: string | null | undefined,
  revealed: boolean,
): { displayValue: string; showReveal: boolean; inputType: "text" | "password" } {
  const displayValue = secretInputDisplayValue(value);
  const showReveal = canRevealSecretValue(value);
  return {
    displayValue,
    showReveal,
    inputType: revealed && showReveal ? "text" : "password",
  };
}

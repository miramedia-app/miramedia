import { describe, expect, it, vi } from "vitest";
import {
  legacySettingsLoadedFromMisc,
  retrySettingsReads,
  settingsReadViewState,
} from "./settings-read-state";

describe("settingsReadViewState", () => {
  it("is pending while either request is still pending", () => {
    expect(
      settingsReadViewState({
        settingsIsPending: true,
        settingsIsError: false,
        schemaIsPending: true,
        schemaIsError: false,
      }),
    ).toBe("pending");
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: false,
        schemaIsPending: true,
        schemaIsError: false,
      }),
    ).toBe("pending");
    expect(
      settingsReadViewState({
        settingsIsPending: true,
        settingsIsError: false,
        schemaIsPending: false,
        schemaIsError: false,
      }),
    ).toBe("pending");
  });

  it("surfaces error when the settings read fails (never perpetual pending)", () => {
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: true,
        schemaIsPending: false,
        schemaIsError: false,
      }),
    ).toBe("error");
  });

  it("surfaces error when the schema read fails", () => {
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: false,
        schemaIsPending: false,
        schemaIsError: true,
      }),
    ).toBe("error");
  });

  it("prefers error over pending when one query already failed", () => {
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: true,
        schemaIsPending: true,
        schemaIsError: false,
      }),
    ).toBe("error");
  });

  it("is ready only when both queries succeeded", () => {
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: false,
        schemaIsPending: false,
        schemaIsError: false,
      }),
    ).toBe("ready");
  });

  it("treats successful empty optional sections as ready, not loading", () => {
    // Query status alone drives readiness — empty auth/notifications/etc. must
    // not keep the page in pending (the old misc-gated check).
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: false,
        schemaIsPending: false,
        schemaIsError: false,
      }),
    ).toBe("ready");
    expect(legacySettingsLoadedFromMisc({ misc: {} })).toBe(true);
  });
});

describe("legacySettingsLoadedFromMisc", () => {
  it("reproduces the perpetual-skeleton bug after a failed read", () => {
    // Failed OpenAPI call → no data → coerced to {} → !!misc is false forever.
    expect(legacySettingsLoadedFromMisc({})).toBe(false);
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: true,
        schemaIsPending: false,
        schemaIsError: false,
      }),
    ).toBe("error");
  });

  it("never treats failed empty data as an editable ready form", () => {
    expect(legacySettingsLoadedFromMisc({})).toBe(false);
    expect(
      settingsReadViewState({
        settingsIsPending: false,
        settingsIsError: true,
        schemaIsPending: false,
        schemaIsError: false,
      }),
    ).not.toBe("ready");
  });
});

describe("retrySettingsReads", () => {
  it("refetches only the failed settings query", () => {
    const refetchSettings = vi.fn();
    const refetchSchema = vi.fn();
    retrySettingsReads({
      settingsIsError: true,
      schemaIsError: false,
      refetchSettings,
      refetchSchema,
    });
    expect(refetchSettings).toHaveBeenCalledTimes(1);
    expect(refetchSchema).not.toHaveBeenCalled();
  });

  it("refetches only the failed schema query", () => {
    const refetchSettings = vi.fn();
    const refetchSchema = vi.fn();
    retrySettingsReads({
      settingsIsError: false,
      schemaIsError: true,
      refetchSettings,
      refetchSchema,
    });
    expect(refetchSettings).not.toHaveBeenCalled();
    expect(refetchSchema).toHaveBeenCalledTimes(1);
  });

  it("refetches both when both failed", () => {
    const refetchSettings = vi.fn();
    const refetchSchema = vi.fn();
    retrySettingsReads({
      settingsIsError: true,
      schemaIsError: true,
      refetchSettings,
      refetchSchema,
    });
    expect(refetchSettings).toHaveBeenCalledTimes(1);
    expect(refetchSchema).toHaveBeenCalledTimes(1);
  });
});

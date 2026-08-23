import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
  },
}));

import {
  DEFAULT_FEATURES,
  fetchFeatures,
  featuresReady,
  resolveFeatures,
  type Features,
} from "@/components/providers/features-provider";

const sampleFeatures: Features = {
  requests: true,
  subtitles: true,
  notifications: false,
  watchlists: false,
  custom_lists: false,
  watch_next: false,
  watch_next_include_specials: true,
  upcoming: false,
  upcoming_default_past_days: 3,
  upcoming_default_future_days: 14,
  continue_watching: true,
  streaming: true,
  downloads: true,
};

const FAIL_CLOSED_FLAGS = [
  "requests",
  "subtitles",
  "notifications",
  "watchlists",
  "custom_lists",
  "watch_next",
  "upcoming",
  "continue_watching",
  "streaming",
  "downloads",
] as const;

describe("fetchFeatures", () => {
  it("resolves with data when the client returns { data }", async () => {
    mocks.get.mockResolvedValueOnce({ data: sampleFeatures });
    await expect(fetchFeatures()).resolves.toEqual(sampleFeatures);
  });

  it("throws when the client returns { error }", async () => {
    const error = { detail: "unavailable" };
    mocks.get.mockResolvedValueOnce({ error });
    await expect(fetchFeatures()).rejects.toBe(error);
  });
});

describe("resolveFeatures", () => {
  it("returns data when present even if isError is true", () => {
    expect(resolveFeatures({ data: sampleFeatures, isError: true })).toBe(sampleFeatures);
  });

  it("returns fail-closed flags when data is undefined and isError is true", () => {
    const resolved = resolveFeatures({ data: undefined, isError: true });
    for (const flag of FAIL_CLOSED_FLAGS) {
      expect(resolved[flag]).toBe(false);
    }
  });

  it("returns DEFAULT_FEATURES when data is undefined and not error", () => {
    expect(resolveFeatures({ data: undefined, isError: false })).toEqual(DEFAULT_FEATURES);
  });
});

describe("featuresReady", () => {
  it("is false while pending or on error", () => {
    expect(featuresReady(true, false)).toBe(false);
    expect(featuresReady(false, true)).toBe(false);
    expect(featuresReady(true, true)).toBe(false);
  });

  it("is true only after a settled success", () => {
    expect(featuresReady(false, false)).toBe(true);
  });
});

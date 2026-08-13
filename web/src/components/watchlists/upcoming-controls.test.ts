import { describe, expect, it } from "vitest";
import { toIsoDate } from "@/lib/upcoming";
import {
  defaultWindow,
  nextRangeStep,
  presetWindow,
  resolveUpcomingWindow,
} from "./upcoming-controls";

const jan = (day: number) => new Date(2026, 0, day, 12);

describe("nextRangeStep", () => {
  it("restarts from the clicked day when a complete range is showing", () => {
    // react-day-picker reports this as an extension of the committed range;
    // the user means "pick a new start".
    const step = nextRangeStep(
      { from: jan(1), to: jan(10) },
      { from: jan(1), to: jan(20) },
      jan(20),
    );
    expect(step.commit).toBeNull();
    expect(step.draft).toEqual({ from: jan(20), to: undefined });
  });

  it("holds a half-picked range without committing", () => {
    const step = nextRangeStep(undefined, { from: jan(5), to: undefined }, jan(5));
    expect(step.commit).toBeNull();
    expect(step.draft).toEqual({ from: jan(5), to: undefined });
  });

  it("commits and clears the draft once the second end is picked", () => {
    const step = nextRangeStep(
      { from: jan(5), to: undefined },
      { from: jan(5), to: jan(9) },
      jan(9),
    );
    expect(step.commit).toEqual({ start: "2026-01-05", end: "2026-01-09" });
    expect(step.draft).toBeUndefined();
  });

  it("clears the draft when the selection is dropped entirely", () => {
    const step = nextRangeStep({ from: jan(5), to: undefined }, undefined, jan(5));
    expect(step).toEqual({ draft: undefined, commit: null });
  });
});

describe("presetWindow", () => {
  it("spans the requested past/future day offsets around today", () => {
    const { start, end } = presetWindow(7, 28);
    const days = (Date.parse(end) - Date.parse(start)) / 86_400_000;
    expect(days).toBe(35);
  });
});

describe("defaultWindow", () => {
  it("starts today and runs thirty days forward", () => {
    const { start, end } = defaultWindow();
    expect(start).toBe(toIsoDate(new Date()));
    expect((Date.parse(end) - Date.parse(start)) / 86_400_000).toBe(30);
  });
});

describe("resolveUpcomingWindow", () => {
  const override = { start: "2026-02-01", end: "2026-02-14" };

  it("returns null while features are pending and there is no override", () => {
    expect(
      resolveUpcomingWindow({
        override: null,
        featuresReady: false,
        pastDays: 7,
        futureDays: 90,
      }),
    ).toBeNull();
  });

  it("returns the server default window once features are ready", () => {
    expect(
      resolveUpcomingWindow({
        override: null,
        featuresReady: true,
        pastDays: 7,
        futureDays: 90,
      }),
    ).toEqual(defaultWindow(7, 90));
  });

  it("returns the override even while features are still pending", () => {
    expect(
      resolveUpcomingWindow({
        override,
        featuresReady: false,
        pastDays: 7,
        futureDays: 90,
      }),
    ).toEqual(override);
  });

  it("returns the override instead of the server default after features are ready", () => {
    expect(
      resolveUpcomingWindow({
        override,
        featuresReady: true,
        pastDays: 7,
        futureDays: 90,
      }),
    ).toEqual(override);
  });
});

import { describe, expect, it } from "vitest";

import {
  indexerSearchMatch,
  siteHealthGroup,
  sitePriority,
  siteTestFacetValue,
  siteTypeLabel,
} from "@/lib/indexers";
import type { Site } from "@/lib/indexers";

function site(over: Partial<Site> = {}): Site {
  return {
    id: "s1",
    name: "Example Tracker",
    url: "https://tracker.example.com",
    supports_tv: true,
    supports_movies: true,
    site_type: "torznab",
    enabled: true,
    ...over,
  };
}

describe("siteTypeLabel", () => {
  it("labels native and torznab types", () => {
    expect(siteTypeLabel.native).toBe("System");
    expect(siteTypeLabel.torznab).toBe("Custom");
  });
});

describe("indexerSearchMatch", () => {
  it("matches on name or url, case-insensitively", () => {
    expect(indexerSearchMatch(site(), "example")).toBe(true);
    expect(indexerSearchMatch(site({ name: "Foo" }), "tracker.example")).toBe(true);
    expect(indexerSearchMatch(site({ name: "Foo", url: "https://x" }), "example")).toBe(false);
  });
});

describe("siteTestFacetValue", () => {
  it("buckets error vs everything else as ok", () => {
    expect(siteTestFacetValue(site({ last_test_status: "error" }))).toBe("error");
    expect(siteTestFacetValue(site({ last_test_status: "ok" }))).toBe("ok");
    expect(siteTestFacetValue(site({ last_test_status: null }))).toBe("ok");
  });
});

describe("siteHealthGroup", () => {
  it("prioritizes failed, then healthy, then untested", () => {
    expect(siteHealthGroup(site({ last_test_status: "error" })).key).toBe("failed");
    expect(
      siteHealthGroup(site({ last_test_status: "ok", last_success_at: "2026-01-01" })).key,
    ).toBe("healthy");
    expect(siteHealthGroup(site()).key).toBe("untested");
  });

  it("orders failed < healthy < untested", () => {
    const failed = siteHealthGroup(site({ last_test_status: "error" })).sortOrder;
    const healthy = siteHealthGroup(site({ last_success_at: "2026-01-01" })).sortOrder;
    const untested = siteHealthGroup(site()).sortOrder;
    expect(failed).toBeLessThan(healthy);
    expect(healthy).toBeLessThan(untested);
  });
});

describe("sitePriority", () => {
  it("defaults unset priority to 100", () => {
    expect(sitePriority(site())).toBe(100);
    expect(sitePriority(site({ priority: 5 }))).toBe(5);
    expect(sitePriority(site({ priority: null }))).toBe(100);
  });
});

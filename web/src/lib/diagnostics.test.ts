import { describe, expect, it } from "vitest";

import {
  DIAGNOSTICS_ERROR_MESSAGE,
  STORAGE_HEALTH_COPY,
  STORAGE_HEALTH_ERROR_MESSAGE,
  STORAGE_HEALTH_FORBIDDEN_ACTIONS,
  formatBytes,
  humanizeCron,
  parseDiagnosticsTab,
  parseStorageHealthSearch,
  storageHealthAllClear,
  storageHealthFilterParam,
  storageHealthHasMutationControls,
  storageHealthImportsHref,
  storageHealthTitleHref,
  storageHealthUnknownHint,
  storageHealthViewState,
  volumeUsedPercent,
} from "@/lib/diagnostics";

describe("storageHealthViewState", () => {
  it("keeps pending summary unknown instead of claiming zero", () => {
    const view = storageHealthViewState({
      isPending: true,
      isError: false,
      data: null,
    });
    expect(view).toEqual({ status: "pending" });
  });

  it("renders a safe terminal failure without backend details", () => {
    const view = storageHealthViewState({
      isPending: false,
      isError: true,
      data: null,
    });
    expect(view).toEqual({ status: "error", message: STORAGE_HEALTH_ERROR_MESSAGE });
  });

  it("does not coerce unknown counts to healthy", () => {
    const view = storageHealthViewState({
      isPending: false,
      isError: false,
      data: {
        imported: 10,
        healthy: 0,
        unknown: 10,
        corrupt: 0,
        orphaned: 0,
        pending: 0,
        missing: null,
      },
    });
    expect(view.status).toBe("success");
    if (view.status === "success") {
      expect(view.counts.unknown).toBe(10);
      expect(view.counts.healthy).toBe(0);
      expect(view.counts.missing).toBeNull();
    }
  });

  it("materializes OpenAPI-optional missing as explicit null", () => {
    const view = storageHealthViewState({
      isPending: false,
      isError: false,
      data: {
        imported: 1,
        healthy: 1,
        unknown: 0,
        corrupt: 0,
        orphaned: 0,
        pending: 0,
      },
    });
    expect(view.status).toBe("success");
    if (view.status === "success") {
      expect(view.counts.missing).toBeNull();
    }
  });
});

describe("parseStorageHealthSearch", () => {
  it("reads page, facets, search, detail, and tab from the URL", () => {
    const params = new URLSearchParams();
    params.set("tab", "database");
    params.set("p", "2");
    params.set("ps", "50");
    params.set("f", "state:corrupt&media_type:show");
    params.set("q", "Sev");
    params.set("mt", "show");
    params.set("fid", "abc");
    const parsed = parseStorageHealthSearch(params);
    expect(parsed.tab).toBe("database");
    expect(parsed.page).toBe(2);
    expect(parsed.offset).toBe(50);
    expect(parsed.limit).toBe(50);
    expect(parsed.state).toBe("corrupt");
    expect(parsed.mediaType).toBe("show");
    expect(parsed.q).toBe("Sev");
    expect(parsed.detailMediaType).toBe("show");
    expect(parsed.detailFileId).toBe("abc");
  });

  it("caps page size at 100", () => {
    const parsed = parseStorageHealthSearch(new URLSearchParams("ps=200"));
    expect(parsed.limit).toBe(100);
  });
});

describe("parseDiagnosticsTab", () => {
  it("defaults to storage and rejects unknown tabs", () => {
    expect(parseDiagnosticsTab(new URLSearchParams())).toBe("storage");
    expect(parseDiagnosticsTab(new URLSearchParams("tab=scheduler"))).toBe("scheduler");
    expect(parseDiagnosticsTab(new URLSearchParams("tab=nope"))).toBe("storage");
  });
});

describe("storage health copy", () => {
  it("does not advertise repair actions", () => {
    const haystack = [
      STORAGE_HEALTH_COPY.missingNote,
      STORAGE_HEALTH_COPY.integrityOff,
      STORAGE_HEALTH_COPY.integrityOnUnknown,
      STORAGE_HEALTH_COPY.allClear,
      STORAGE_HEALTH_COPY.inaccessible,
      STORAGE_HEALTH_COPY.hashingDisabled,
      DIAGNOSTICS_ERROR_MESSAGE,
      ...STORAGE_HEALTH_FORBIDDEN_ACTIONS,
    ].join(" ");
    expect(storageHealthHasMutationControls(haystack)).toBe(true);
    expect(
      storageHealthHasMutationControls(
        [
          STORAGE_HEALTH_COPY.missingNote,
          STORAGE_HEALTH_COPY.integrityOff,
          STORAGE_HEALTH_COPY.integrityOnUnknown,
          STORAGE_HEALTH_COPY.allClear,
          STORAGE_HEALTH_COPY.inaccessible,
          STORAGE_HEALTH_COPY.hashingDisabled,
          DIAGNOSTICS_ERROR_MESSAGE,
        ].join(" "),
      ),
    ).toBe(false);
  });

  it("treats integrity-off unknown as informational", () => {
    expect(storageHealthUnknownHint({ integrityEnabled: false, unknown: 4 })).toBe(
      STORAGE_HEALTH_COPY.integrityOff,
    );
    expect(storageHealthUnknownHint({ integrityEnabled: true, unknown: 4 })).toBe(
      STORAGE_HEALTH_COPY.integrityOnUnknown,
    );
    expect(storageHealthUnknownHint({ integrityEnabled: true, unknown: 0 })).toBeNull();
  });

  it("links pending recovery to Imports and titles to existing pages", () => {
    expect(storageHealthImportsHref()).toBe("/dashboard/imports");
    expect(storageHealthTitleHref("show", "s1")).toBe("/dashboard/shows/s1");
    expect(storageHealthTitleHref("movie", "m1")).toBe("/dashboard/movies/m1");
  });

  it("builds a DataList filter param for summary-card drill-down", () => {
    expect(storageHealthFilterParam({ state: "pending" })).toBe("state:pending");
    expect(
      storageHealthAllClear({
        imported: 0,
        healthy: 0,
        unknown: 0,
        corrupt: 0,
        orphaned: 0,
        pending: 0,
        missing: null,
      }),
    ).toBe(true);
  });
});

describe("formatBytes", () => {
  it("formats common sizes and blanks invalid values", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(10 * 1024 * 1024)).toBe("10 MB");
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });
});

describe("volumeUsedPercent", () => {
  it("returns a 0-100 share when totals are known", () => {
    expect(volumeUsedPercent({ used_bytes: 25, total_bytes: 100 })).toBe(25);
    expect(volumeUsedPercent({ used_bytes: null, total_bytes: 100 })).toBeNull();
    expect(volumeUsedPercent({ used_bytes: 1, total_bytes: 0 })).toBeNull();
  });
});

describe("humanizeCron", () => {
  it("describes the schedules MiraMedia actually uses", () => {
    expect(humanizeCron("* * * * *")).toBe("Every minute");
    expect(humanizeCron("*/5 * * * *")).toBe("Every 5 minutes");
    expect(humanizeCron("0 */6 * * *")).toBe("Every 6 hours");
    expect(humanizeCron("0 3 * * *")).toBe("Daily at 03:00");
    expect(humanizeCron("0 0 * * 1")).toBe("Weekly on Monday at 00:00");
    expect(humanizeCron(null)).toBe("—");
  });
});

describe("diagnostics page source", () => {
  it("does not mount repair or mutation controls", async () => {
    const { readFile } = await import("node:fs/promises");
    const source = await readFile(
      new URL("../app/(dashboard)/dashboard/system/diagnostics/storage-panel.tsx", import.meta.url),
      "utf8",
    );
    expect(source).not.toMatch(/ImportRowActions|resolveIntegrity|rebaseline|reimport|reacquire/);
  });
});

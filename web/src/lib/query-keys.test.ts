import { describe, expect, it } from "vitest";

import { qk } from "@/lib/query-keys";

describe("imports query keys", () => {
  it("keys the list per tab/page and exposes a prefix that matches them", () => {
    const key = qk.imports.list("review", 0, 50) as readonly unknown[];
    expect(key).toEqual(["imports", "list", "review", 0, 50]);
    const prefix = qk.imports.list();
    expect(key.slice(0, prefix.length)).toEqual([...prefix]);
  });

  it("sits under the imports prefix so a global imports refresh covers everything", () => {
    for (const key of [
      qk.imports.list("all") as readonly unknown[],
      qk.imports.counts() as readonly unknown[],
      qk.imports.scan() as readonly unknown[],
    ]) {
      expect(key.slice(0, qk.imports.all.length)).toEqual([...qk.imports.all]);
    }
  });
});

describe("diagnostics query keys", () => {
  it("keys storage, database, and scheduler under one prefix", () => {
    const summary = qk.diagnostics.storage.summary() as readonly unknown[];
    const list = qk.diagnostics.storage.list({ offset: 0, limit: 50 }) as readonly unknown[];
    const detail = qk.diagnostics.storage.detail("show", "abc") as readonly unknown[];
    const database = qk.diagnostics.database() as readonly unknown[];
    const scheduler = qk.diagnostics.scheduler() as readonly unknown[];
    expect(summary.slice(0, qk.diagnostics.all.length)).toEqual([...qk.diagnostics.all]);
    expect(list.slice(0, qk.diagnostics.all.length)).toEqual([...qk.diagnostics.all]);
    expect(detail.slice(0, qk.diagnostics.all.length)).toEqual([...qk.diagnostics.all]);
    expect(database.slice(0, qk.diagnostics.all.length)).toEqual([...qk.diagnostics.all]);
    expect(scheduler.slice(0, qk.diagnostics.all.length)).toEqual([...qk.diagnostics.all]);
  });
});

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

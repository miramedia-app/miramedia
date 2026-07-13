import { describe, expect, it } from "vitest";

import { qk } from "@/lib/query-keys";

describe("integrity query keys", () => {
  it("keys one entry per server page", () => {
    expect(qk.imports.integrity(0, 50)).toEqual(["imports", "integrity", "mismatches", 0, 50]);
    expect(qk.imports.integrity(50, 50)).toEqual(["imports", "integrity", "mismatches", 50, 50]);
    expect(qk.imports.integrity(0, 50)).not.toEqual(qk.imports.integrity(50, 50));
  });

  it("exposes a prefix that matches every page key", () => {
    const prefix = qk.imports.integrity();
    expect(prefix).toEqual(["imports", "integrity", "mismatches"]);
    // TanStack invalidates/cancels/removes by prefix: every page key must start
    // with it, or a role loss would leave a cached admin page behind.
    for (const offset of [0, 50, 100]) {
      const key = qk.imports.integrity(offset, 50) as readonly unknown[];
      expect(key.slice(0, prefix.length)).toEqual([...prefix]);
    }
  });

  it("sits under the imports prefix so a global imports refresh covers it", () => {
    const key = qk.imports.integrity(0, 50) as readonly unknown[];
    expect(key.slice(0, qk.imports.all.length)).toEqual([...qk.imports.all]);
  });
});

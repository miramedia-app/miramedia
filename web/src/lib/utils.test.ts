import { describe, expect, it } from "vitest";

import { formatCastLine, unescapeHtmlEntities } from "@/lib/utils";

describe("unescapeHtmlEntities", () => {
  it("decodes a named apostrophe entity", () => {
    expect(unescapeHtmlEntities("Emma D&apos;Arcy")).toBe("Emma D'Arcy");
  });
});

describe("formatCastLine", () => {
  it("joins names and decodes HTML entities", () => {
    expect(formatCastLine(["Matt Smith", "Emma D&apos;Arcy", "Olivia Cooke"])).toBe(
      "Matt Smith, Emma D'Arcy, Olivia Cooke",
    );
  });
});

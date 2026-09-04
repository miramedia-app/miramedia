import { describe, expect, it } from "vitest";

import { apiErrorDetail } from "@/lib/api/error-detail";

describe("apiErrorDetail", () => {
  it("prefers a FastAPI string detail", () => {
    expect(apiErrorDetail({ detail: "already exists" }, "fallback")).toBe("already exists");
  });

  it("falls back when detail is missing or not a string", () => {
    expect(apiErrorDetail(undefined, "fallback")).toBe("fallback");
    expect(apiErrorDetail({ detail: [{ msg: "x" }] }, "fallback")).toBe("fallback");
    expect(apiErrorDetail({ detail: "  " }, "fallback")).toBe("fallback");
  });
});

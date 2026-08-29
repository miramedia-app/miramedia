import { describe, expect, it } from "vitest";
import * as React from "react";

import { flattenActionNodes, resolveMobileLayout, scrollMinWidth } from "./mobile-utils";
import type { ColumnDef } from "./types";

const col = (id: string, width: string, mobile?: ColumnDef<unknown>["mobile"]) =>
  ({ id, width, mobile, render: () => null }) as ColumnDef<unknown>;

describe("resolveMobileLayout", () => {
  it("honours explicit roles and drops hidden columns", () => {
    const layout = resolveMobileLayout([
      col("status", "112px", { role: "meta", order: 0 }),
      col("name", "minmax(0,1fr)", { role: "title" }),
      col("note", "200px", { role: "hidden" }),
      col("by", "160px", { role: "subtitle" }),
      col("type", "72px", { role: "meta", order: 1 }),
    ]);
    expect(layout.title?.id).toBe("name");
    expect(layout.subtitle?.id).toBe("by");
    expect(layout.meta.map((c) => c.id)).toEqual(["status", "type"]);
  });

  it("splits status and progress roles out of meta", () => {
    const layout = resolveMobileLayout([
      col("name", "minmax(0,1fr)", { role: "title" }),
      col("status", "112px", { role: "status" }),
      col("progress", "220px", { role: "progress" }),
      col("type", "72px", { role: "meta" }),
    ]);
    expect(layout.status?.id).toBe("status");
    expect(layout.progress?.id).toBe("progress");
    expect(layout.meta.map((c) => c.id)).toEqual(["type"]);
  });

  it("infers the flexible column as title when nothing is annotated", () => {
    const layout = resolveMobileLayout([
      col("ts", "180px"),
      col("msg", "minmax(0,1fr)"),
      col("level", "110px"),
    ]);
    expect(layout.title?.id).toBe("msg");
    expect(layout.subtitle).toBeNull();
    expect(layout.meta.map((c) => c.id)).toEqual(["ts", "level"]);
  });

  it("falls back to the first column when no width is flexible", () => {
    const layout = resolveMobileLayout([col("a", "80px"), col("b", "80px")]);
    expect(layout.title?.id).toBe("a");
    expect(layout.meta.map((c) => c.id)).toEqual(["b"]);
  });

  it("keeps un-annotated columns as meta alongside partially annotated ones", () => {
    const layout = resolveMobileLayout([
      col("name", "minmax(0,1fr)", { role: "title" }),
      col("type", "72px"),
    ]);
    expect(layout.title?.id).toBe("name");
    expect(layout.meta.map((c) => c.id)).toEqual(["type"]);
  });
});

describe("flattenActionNodes", () => {
  it("unwraps fragments and arrays, skipping null/boolean", () => {
    const a = React.createElement("button", { key: "a" });
    const b = React.createElement("button", { key: "b" });
    const c = React.createElement("button", { key: "c" });
    const node = React.createElement(React.Fragment, null, a, null, false, [b, c]);
    expect(flattenActionNodes(node)).toHaveLength(3);
    expect(flattenActionNodes(null)).toEqual([]);
  });
});

describe("scrollMinWidth", () => {
  it("sums px tracks and floors flexible tracks", () => {
    expect(scrollMinWidth(["24px", "180px", "minmax(0,1fr)"])).toBe(24 + 180 + 240);
    expect(scrollMinWidth(["minmax(300px,2fr)"], 240)).toBe(300);
  });
});

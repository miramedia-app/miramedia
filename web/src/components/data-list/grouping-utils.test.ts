import { describe, expect, it } from "vitest";
import { countGroups } from "./grouping-utils";

type Row = { id: number; status: string };

const getGroup = (r: Row) => ({ key: r.status });

describe("countGroups", () => {
  it("returns an empty map for an empty set", () => {
    expect(countGroups([], getGroup).size).toBe(0);
  });

  it("counts the full set, not just one page", () => {
    const rows: Row[] = Array.from({ length: 25 }, (_, i) => ({
      id: i,
      status: i % 5 === 0 ? "downloading" : "done",
    }));

    const pageSize = 10;
    const page1 = rows.slice(0, pageSize);
    const totals = countGroups(rows, getGroup);

    expect(totals.get("downloading")).toBe(5);
    expect(totals.get("done")).toBe(20);
    // The page-local count is what the header would have shown before.
    expect(countGroups(page1, getGroup).get("downloading")).toBe(2);
  });

  it("keeps groups distinct", () => {
    const rows: Row[] = [
      { id: 1, status: "a" },
      { id: 2, status: "b" },
      { id: 3, status: "a" },
    ];
    expect([...countGroups(rows, getGroup).entries()]).toEqual([
      ["a", 2],
      ["b", 1],
    ]);
  });
});

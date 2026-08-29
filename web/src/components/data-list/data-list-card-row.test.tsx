// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { DataListCardRow } from "./data-list-card-row";
import { MobilePrimaryAction } from "./mobile-utils";
import type { ColumnDef } from "./types";

vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => true }));

interface Row {
  id: string;
  name: string;
  status: string;
  note: string;
  by: string;
}

const item: Row = { id: "1", name: "Alpha", status: "active", note: "secret", by: "james" };

const columns: ColumnDef<Row>[] = [
  { id: "status", width: "112px", mobile: { role: "meta" }, render: (r) => <b>{r.status}</b> },
  { id: "name", width: "minmax(0,1fr)", mobile: { role: "title" }, render: (r) => r.name },
  { id: "note", width: "200px", mobile: { role: "hidden" }, render: (r) => r.note },
  { id: "by", width: "160px", mobile: { role: "subtitle" }, render: (r) => r.by },
];

afterEach(cleanup);

function renderRow(extra: Partial<React.ComponentProps<typeof DataListCardRow<Row>>> = {}) {
  return render(
    <DataListCardRow<Row>
      item={item}
      id="1"
      columns={columns}
      hasSelectColumn={false}
      selectable={false}
      selected={false}
      focused={false}
      density="standard"
      {...extra}
    />,
  );
}

describe("DataListCardRow", () => {
  it("maps roles to title / subtitle / meta slots and omits hidden columns", () => {
    const { container } = renderRow();
    expect(container.querySelector('[data-slot="card-title"]')?.textContent).toBe("Alpha");
    expect(container.querySelector('[data-slot="card-subtitle"]')?.textContent).toBe("james");
    expect(container.querySelector('[data-column="status"]')?.textContent).toBe("active");
    expect(container.textContent).not.toContain("secret");
  });

  it("renders a single action inline", () => {
    const { container } = renderRow({
      renderActions: () => <button>Edit</button>,
    });
    expect(screen.getByText("Edit")).toBeTruthy();
    expect(container.querySelector('[data-slot="card-actions-overflow"]')).toBeNull();
  });

  it("collapses two or more actions into one overflow trigger", () => {
    const { container } = renderRow({
      renderActions: () => (
        <>
          <button>Edit</button>
          <button>Delete</button>
        </>
      ),
    });
    expect(container.querySelector('[data-slot="card-actions-overflow"]')).not.toBeNull();
    expect(screen.queryByText("Edit")).toBeNull();
  });

  it("opens a labelled action sheet from mobileActions and runs the selected one", () => {
    const onSelect = vi.fn();
    const { container } = renderRow({
      renderActions: () => <button>ignored</button>,
      mobileActions: () => [
        { id: "pause", label: "Pause download", onSelect },
        { id: "del", label: "Delete", destructive: true, onSelect: vi.fn() },
      ],
    });
    expect(screen.queryByText("ignored")).toBeNull();
    fireEvent.click(container.querySelector('[data-slot="card-actions-overflow"]')!);
    fireEvent.click(screen.getByText("Pause download"));
    expect(onSelect).toHaveBeenCalled();
  });

  it("places status and progress columns in their own slots", () => {
    const cols: ColumnDef<Row>[] = [
      { id: "name", width: "1fr", mobile: { role: "title" }, render: (r) => r.name },
      { id: "status", width: "1fr", mobile: { role: "status" }, render: (r) => r.status },
      { id: "bar", width: "1fr", mobile: { role: "progress" }, render: () => "50%" },
    ];
    const { container } = renderRow({ columns: cols });
    expect(container.querySelector('[data-slot="card-status"]')?.textContent).toBe("active");
    expect(container.querySelector('[data-slot="card-progress"]')?.textContent).toBe("50%");
    expect(container.querySelector('[data-slot="card-meta"]')).toBeNull();
  });

  it("prefers the column's mobile renderer over the desktop cell", () => {
    const cols: ColumnDef<Row>[] = [
      {
        id: "name",
        width: "1fr",
        mobile: { role: "title", render: (r) => `Mobile ${r.name}` },
        render: (r) => r.name,
      },
    ];
    const { container } = renderRow({ columns: cols });
    expect(container.querySelector('[data-slot="card-title"]')?.textContent).toBe("Mobile Alpha");
  });

  it("toggles expand on full-row tap when expandable, otherwise opens the row", () => {
    const onToggleExpandId = vi.fn();
    const onClickId = vi.fn();
    const { container, unmount } = renderRow({ expandable: true, onToggleExpandId, onClickId });
    fireEvent.click(container.querySelector('[data-slot="card-row"]')!);
    expect(onToggleExpandId).toHaveBeenCalledWith("1");
    expect(onClickId).not.toHaveBeenCalled();
    unmount();

    const { container: c2 } = renderRow({ onClickId });
    fireEvent.click(c2.querySelector('[data-slot="card-row"]')!);
    expect(onClickId).toHaveBeenCalledWith("1");
  });

  it("selection checkbox does not trigger row click", () => {
    const onClickId = vi.fn();
    const onToggleSelectId = vi.fn();
    renderRow({ hasSelectColumn: true, selectable: true, onClickId, onToggleSelectId });
    fireEvent.click(screen.getByLabelText("Select row"));
    expect(onToggleSelectId).toHaveBeenCalled();
    expect(onClickId).not.toHaveBeenCalled();
  });
});

describe("DataListCardRow primary action", () => {
  it("inlines a primary MobileAction and keeps the rest behind a compact overflow", () => {
    const play = vi.fn();
    const { container } = renderRow({
      mobileActions: () => [
        { id: "play", label: "Play", icon: <svg />, onSelect: play, primary: true },
        { id: "delete", label: "Delete", onSelect: () => {}, destructive: true },
      ],
    });
    const primary = screen.getByRole("button", { name: "Play" });
    expect(primary.className).toContain("h-11");
    fireEvent.click(primary);
    expect(play).toHaveBeenCalledTimes(1);
    const overflow = container.querySelector('[data-slot="card-actions-overflow"]');
    expect(overflow?.className).toContain("h-9");
  });

  it("drops the overflow when the primary action is the only one", () => {
    const { container } = renderRow({
      mobileActions: () => [{ id: "play", label: "Play", onSelect: () => {}, primary: true }],
    });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(container.querySelector('[data-slot="card-actions-overflow"]')).toBeNull();
  });

  it("inlines a MobilePrimaryAction-wrapped node from renderActions", () => {
    const { container } = renderRow({
      renderActions: () => (
        <>
          <MobilePrimaryAction>
            <button aria-label="Play">P</button>
          </MobilePrimaryAction>
          <button aria-label="Download">D</button>
          <button aria-label="Delete">X</button>
        </>
      ),
    });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Download" })).toBeNull();
    expect(container.querySelector('[data-slot="card-actions-overflow"]')).not.toBeNull();
  });
});

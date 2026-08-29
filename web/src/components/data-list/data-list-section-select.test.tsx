// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { DataListSectionSelectToggle, useSectionSelectMode } from "./data-list-section-select";

const mobile = vi.hoisted(() => ({ value: true }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => mobile.value }));

function Harness({ onClear }: { onClear: () => void }) {
  const s = useSectionSelectMode(onClear);
  return (
    <>
      <span data-testid="mode">{String(s.selectMode)}</span>
      <DataListSectionSelectToggle selectMode={s.mobileSelectMode} onToggle={s.toggle} />
    </>
  );
}

afterEach(cleanup);

describe("useSectionSelectMode", () => {
  it("mobile: hidden until toggled; leaving select mode clears selection", () => {
    mobile.value = true;
    const onClear = vi.fn();
    render(<Harness onClear={onClear} />);
    expect(screen.getByTestId("mode").textContent).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Select rows" }));
    expect(screen.getByTestId("mode").textContent).toBe("true");
    expect(onClear).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done selecting" }));
    expect(screen.getByTestId("mode").textContent).toBe("false");
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("desktop: always in select mode", () => {
    mobile.value = false;
    render(<Harness onClear={() => {}} />);
    expect(screen.getByTestId("mode").textContent).toBe("true");
  });
});

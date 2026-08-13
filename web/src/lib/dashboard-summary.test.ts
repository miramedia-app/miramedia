import { describe, expect, it } from "vitest";
import {
  DASHBOARD_SUMMARY_ERROR_MESSAGE,
  dashboardImportWarningCounts,
  dashboardSummaryViewState,
  type DashboardSummaryCounts,
} from "./dashboard-summary";

const emptyLibrary: DashboardSummaryCounts = {
  shows: 0,
  movies: 0,
  torrents: 0,
  requestsPending: 0,
  importsFailed: 0,
  importsAmbiguous: 0,
};

const populated: DashboardSummaryCounts = {
  shows: 3,
  movies: 1,
  torrents: 2,
  requestsPending: 0,
  importsFailed: 4,
  importsAmbiguous: 1,
};

describe("dashboardSummaryViewState", () => {
  it("keeps pending summary unknown instead of claiming zero", () => {
    const view = dashboardSummaryViewState({
      isPending: true,
      isError: false,
      data: null,
    });
    expect(view).toEqual({ status: "pending" });
    expect(view).not.toMatchObject({ status: "success" });
  });

  it("renders a safe terminal failure without backend details", () => {
    const view = dashboardSummaryViewState({
      isPending: false,
      isError: true,
      data: null,
    });
    expect(view.status).toBe("error");
    if (view.status !== "error") throw new Error("expected error");
    expect(view.message).toBe(DASHBOARD_SUMMARY_ERROR_MESSAGE);
    expect(view.message).not.toMatch(/stack|traceback|exception|sql|internal/i);
  });

  it("still treats a successful empty library as real zeroes", () => {
    const view = dashboardSummaryViewState({
      isPending: false,
      isError: false,
      data: emptyLibrary,
    });
    expect(view).toEqual({ status: "success", counts: emptyLibrary });
  });

  it("prefers last successful counts over a refetch error", () => {
    const view = dashboardSummaryViewState({
      isPending: false,
      isError: true,
      data: populated,
    });
    expect(view).toEqual({ status: "success", counts: populated });
  });
});

describe("dashboardImportWarningCounts", () => {
  it("suppresses import warnings while summary is unknown", () => {
    expect(
      dashboardImportWarningCounts({
        view: { status: "pending" },
        isSuperuser: true,
      }),
    ).toBeNull();
    expect(
      dashboardImportWarningCounts({
        view: { status: "error", message: DASHBOARD_SUMMARY_ERROR_MESSAGE },
        isSuperuser: true,
      }),
    ).toBeNull();
  });

  it("shows warnings only from successful non-zero import counts", () => {
    expect(
      dashboardImportWarningCounts({
        view: { status: "success", counts: emptyLibrary },
        isSuperuser: true,
      }),
    ).toBeNull();
    expect(
      dashboardImportWarningCounts({
        view: { status: "success", counts: populated },
        isSuperuser: true,
      }),
    ).toEqual({ failed: 4, ambiguous: 1 });
    expect(
      dashboardImportWarningCounts({
        view: { status: "success", counts: populated },
        isSuperuser: false,
      }),
    ).toBeNull();
  });
});

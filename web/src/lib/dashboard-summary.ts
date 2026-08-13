/**
 * Dashboard summary counts are operational facts. Missing or failed reads must
 * stay unknown — never coerce to zero. Zero is only valid after a successful
 * response.
 */

export type DashboardSummaryCounts = {
  shows: number;
  movies: number;
  torrents: number;
  requestsPending: number;
  importsFailed: number;
  importsAmbiguous: number;
};

export type DashboardSummaryView =
  | { status: "pending" }
  | { status: "error"; message: string }
  | { status: "success"; counts: DashboardSummaryCounts };

/** User-safe; never include raw API/exception text. */
export const DASHBOARD_SUMMARY_ERROR_MESSAGE =
  "Unable to load dashboard counts. Check that the server is reachable and try again.";

export function dashboardSummaryViewState(args: {
  isPending: boolean;
  isError: boolean;
  data: DashboardSummaryCounts | null | undefined;
}): DashboardSummaryView {
  if (args.data != null) {
    return { status: "success", counts: args.data };
  }
  if (args.isError) {
    return { status: "error", message: DASHBOARD_SUMMARY_ERROR_MESSAGE };
  }
  if (args.isPending) {
    return { status: "pending" };
  }
  // No data, not pending, not error — treat as unknown rather than zero.
  return { status: "pending" };
}

/**
 * Import warning banner only when counts are known. Pending/error must not
 * surface as "0 failed / 0 ambiguous" (which hides the banner and looks fine).
 */
export function dashboardImportWarningCounts(args: {
  view: DashboardSummaryView;
  isSuperuser: boolean;
}): { failed: number; ambiguous: number } | null {
  if (!args.isSuperuser || args.view.status !== "success") return null;
  const { importsFailed, importsAmbiguous } = args.view.counts;
  if (importsFailed <= 0 && importsAmbiguous <= 0) return null;
  return { failed: importsFailed, ambiguous: importsAmbiguous };
}

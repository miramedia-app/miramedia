import { expect, test } from "@playwright/test";
import { installApiMock, type ApiHandler } from "./fixtures";

// Destructive import recovery: retry a failed torrent import and ignore a scan
// candidate through its confirmation boundary. Both mutate via the imports API;
// the suite asserts the request contracts without a backend.

const TORRENT_ID = "33333333-3333-3333-3333-333333333333";
const SCAN_ID = "44444444-4444-4444-4444-444444444444";
const SCAN_MEDIA_ID = "55555555-5555-5555-5555-555555555555";

const listResponse: ApiHandler = () => ({
  body: {
    total: 2,
    offset: 0,
    limit: 200,
    items: [
      {
        kind: "torrent",
        id: TORRENT_ID,
        backoff_seconds: null,
        entry: {
          torrent_id: TORRENT_ID,
          torrent_title: "Broken Release",
          torrent_status: 4,
          source_dir: "/data/broken",
          media: null,
          progress: {
            total: 1,
            imported: 0,
            failed: 1,
            ambiguous: 0,
            pending: 0,
            last_error: "import failed",
          },
          files: [],
        },
      },
      {
        kind: "scan",
        id: SCAN_ID,
        result: {
          directory: "/data/Scan Beta",
          detected_name: "Scan Beta",
          library_name: "Default",
          size_bytes: 0,
          file_count: 1,
          candidates: [
            {
              media_type: "movie",
              media_id: SCAN_MEDIA_ID,
              media_name: "Scan Beta Movie",
              media_year: 2019,
              confidence: 0.9,
              breakdown: null,
            },
          ],
          provider_candidates: [],
          files: [],
          status: "pending",
        },
      },
    ],
  },
});

test("imports recovery: retry a torrent and ignore a scan through confirmation", async ({
  page,
}) => {
  const mock = await installApiMock(page, {
    "GET /api/v1/imports": listResponse,
    "GET /api/v1/imports/scan/status": () => ({
      body: { state: "idle", items_found: 0, last_error: null },
    }),
    "GET /api/v1/imports/counts": () => ({ body: { importing: 0, import_total: 0 } }),
    "POST /api/v1/imports/resolve": () => ({ body: {} }),
    "POST /api/v1/imports/ignore": () => ({ body: {} }),
  });

  // The scan Ignore path guards on window.confirm — auto-accept it.
  page.on("dialog", (d) => void d.accept());

  await page.goto("/dashboard/imports/");

  // Retry the failed torrent import (scope to its row: the toolbar also has a
  // disabled bulk "Retry").
  const torrentRow = page.getByRole("row").filter({ hasText: "Unlinked" });
  await expect(torrentRow).toBeVisible();
  await torrentRow.getByRole("button", { name: "Retry" }).click();

  await expect.poll(() => mock.find("POST /api/v1/imports/resolve")).toBeTruthy();
  const retry = mock.find("POST /api/v1/imports/resolve");
  expect(JSON.parse(retry?.postData ?? "{}")).toMatchObject({
    kind: "torrent",
    id: TORRENT_ID,
    action: "retry",
  });

  // Ignore the scan candidate through its ⋮ menu + confirmation.
  const scanRow = page.getByRole("row").filter({ hasText: "Scan Beta Movie" });
  await scanRow.getByRole("button").last().click();
  await page.getByTestId("import-scan-ignore").click();

  await expect.poll(() => mock.find("POST /api/v1/imports/ignore")).toBeTruthy();
  const ignore = mock.find("POST /api/v1/imports/ignore");
  expect(JSON.parse(ignore?.postData ?? "{}")).toMatchObject({
    kind: "scan",
    id: SCAN_ID,
    delete_files: false,
  });

  expect(mock.unhandled).toEqual([]);
});

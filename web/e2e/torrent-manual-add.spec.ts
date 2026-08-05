import { expect, test } from "@playwright/test";
import { installApiMock } from "./fixtures";

// Manual torrent acquisition: paste a magnet, parse it (multipart), pick the
// returned candidate, confirm the download, and assert both the outgoing
// request contracts and the success transition — with no backend.

const CANDIDATE_ID = "11111111-1111-1111-1111-111111111111";
const DOWNLOAD_TOKEN = "22222222-2222-2222-2222-222222222222";
// Zeroed btih hash — a syntactically valid magnet that is not a real credential.
const MAGNET = "magnet:?xt=urn:btih:0000000000000000000000000000000000000000&dn=Example.Show.S01";

test("manual add: parse a magnet then download the selected candidate", async ({ page }) => {
  const mock = await installApiMock(page, {
    "GET /api/v1/torrents": () => ({ body: [], headers: { "x-total-count": "0" } }),
    "POST /api/v1/torrents/manual/parse": () => ({
      body: {
        download_token: DOWNLOAD_TOKEN,
        title: "Example Show S01",
        quality: 2,
        seasons: [1],
        episodes: [],
        candidates: [
          {
            media_type: "show",
            media_id: CANDIDATE_ID,
            media_name: "Example Show",
            media_year: 2020,
            confidence: 1,
            breakdown: null,
          },
        ],
      },
    }),
    "POST /api/v1/torrents/manual/download": () => ({ body: {} }),
    // Fetched by the review step's file-path/library selector once a show
    // candidate is selected.
    "GET /api/v1/shows/libraries": () => ({ body: [] }),
  });

  await page.goto("/dashboard/torrents/");

  await page.getByTestId("add-torrent-trigger").click();
  await page.locator("#magnet-link").fill(MAGNET);
  await page.getByTestId("parse-torrent-submit").click();

  // Review step rendered from the parse response.
  await expect(page.getByText("Example Show S01")).toBeVisible();

  const parse = mock.find("POST /api/v1/torrents/manual/parse");
  expect(parse?.postData ?? "").toContain("magnet_link");
  expect(parse?.postData ?? "").toContain(MAGNET);

  // Select the returned candidate, then confirm the download.
  await page.getByRole("radio").first().check();
  await page.getByTestId("download-torrent-submit").click();

  // Success closes the dialog; assert the download request contract.
  await expect(page.getByRole("dialog")).toBeHidden();

  const download = mock.find("POST /api/v1/torrents/manual/download");
  expect(download).toBeTruthy();
  const body = JSON.parse(download?.postData ?? "{}");
  expect(body).toMatchObject({
    download_token: DOWNLOAD_TOKEN,
    media_type: "show",
    media_id: CANDIDATE_ID,
  });

  expect(mock.unhandled).toEqual([]);
});

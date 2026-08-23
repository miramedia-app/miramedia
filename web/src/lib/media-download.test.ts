import { describe, expect, it } from "vitest";

import { importedFileRowActions, mediaStreamDownloadUrl } from "@/lib/media-download";

describe("mediaStreamDownloadUrl", () => {
  it("builds the movie download URL with an encoded file id", () => {
    expect(
      mediaStreamDownloadUrl({
        mediaType: "movie",
        mediaId: "movie-1",
        fileId: "file/1",
        apiUrl: "http://api.example",
      }),
    ).toBe("http://api.example/api/v1/streams/movies/movie-1?file_id=file%2F1&download=true");
  });

  it("builds the episode download URL for show media", () => {
    expect(
      mediaStreamDownloadUrl({
        mediaType: "show",
        mediaId: "episode-1",
        fileId: "file-2",
        apiUrl: "",
      }),
    ).toBe("/api/v1/streams/episodes/episode-1?file_id=file-2&download=true");
  });
});

describe("importedFileRowActions", () => {
  it("shows player and download independently for each flag combination", () => {
    expect(importedFileRowActions({ streaming: true, downloads: true, imported: true })).toEqual({
      showPlayer: true,
      showDownload: false,
    });
    expect(importedFileRowActions({ streaming: true, downloads: false, imported: true })).toEqual({
      showPlayer: true,
      showDownload: false,
    });
    expect(importedFileRowActions({ streaming: false, downloads: true, imported: true })).toEqual({
      showPlayer: false,
      showDownload: true,
    });
    expect(importedFileRowActions({ streaming: false, downloads: false, imported: true })).toEqual({
      showPlayer: false,
      showDownload: false,
    });
  });

  it("exposes neither control when the file is not imported", () => {
    expect(importedFileRowActions({ streaming: true, downloads: true, imported: false })).toEqual({
      showPlayer: false,
      showDownload: false,
    });
  });
});

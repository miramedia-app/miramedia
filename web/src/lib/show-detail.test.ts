import { describe, expect, it } from "vitest";

import {
  buildTreeRows,
  classifyWatchedSelection,
  fileKey,
  seasonHasAllSubtitles,
  subKey,
  subtitleLanguagesByEpisode,
} from "@/lib/show-detail";
import type { EpisodeFile, Season, SubtitleFile } from "@/lib/show-detail";

function episode(over: Partial<Season["episodes"][number]> = {}): Season["episodes"][number] {
  return {
    id: "ep1",
    number: 1,
    title: "Pilot",
    downloaded: false,
    skipped: false,
    status: "wanted",
    ...over,
  } as Season["episodes"][number];
}

function season(over: Partial<Season> = {}): Season {
  return {
    id: "s1",
    number: 1,
    skipped: false,
    status: "wanted",
    episodes: [episode()],
    ...over,
  } as Season;
}

function sub(over: Partial<SubtitleFile> = {}): SubtitleFile {
  return { file_name: "a.srt", language: "en", ...over } as SubtitleFile;
}

function file(over: Partial<EpisodeFile> = {}): EpisodeFile {
  return {
    id: "f1",
    episode_id: "ep1",
    file_name: "ep.mkv",
    file_status: "imported",
    quality: 2,
    ...over,
  } as EpisodeFile;
}

describe("key helpers", () => {
  it("namespaces file and subtitle keys", () => {
    expect(fileKey("abc")).toBe("file:abc");
    expect(subKey("ep1", "a.srt")).toBe("ep1:sub:a.srt");
  });
});

describe("subtitleLanguagesByEpisode", () => {
  it("dedupes and sorts languages per episode", () => {
    const result = subtitleLanguagesByEpisode({
      ep1: [sub({ language: "fr" }), sub({ language: "en" }), sub({ language: "fr" })],
      ep2: [],
    });
    expect(result).toEqual({ ep1: ["en", "fr"], ep2: [] });
  });
});

describe("seasonHasAllSubtitles", () => {
  it("is false when no episode is downloaded", () => {
    const s = season({ episodes: [episode({ downloaded: false })] });
    expect(seasonHasAllSubtitles(s, { ep1: ["en"] })).toBe(false);
  });

  it("is true only when every downloaded episode has a subtitle", () => {
    const s = season({
      episodes: [
        episode({ id: "ep1", downloaded: true }),
        episode({ id: "ep2", downloaded: true }),
        episode({ id: "ep3", downloaded: false }),
      ],
    });
    expect(seasonHasAllSubtitles(s, { ep1: ["en"], ep2: ["en"] })).toBe(true);
    expect(seasonHasAllSubtitles(s, { ep1: ["en"], ep2: [] })).toBe(false);
  });
});

describe("classifyWatchedSelection", () => {
  const specials = season({
    id: "s0",
    number: 0,
    episodes: [
      episode({ id: "sp1", number: 1, title: "Special 1" }),
      episode({ id: "sp2", number: 2, title: "Special 2" }),
    ],
  });
  const season1 = season({
    id: "s1",
    number: 1,
    episodes: [episode({ id: "s1e1", number: 1 }), episode({ id: "s1e2", number: 2, title: "E2" })],
  });
  const season2 = season({
    id: "s2",
    number: 2,
    episodes: [
      episode({ id: "s2e1", number: 1, title: "S2E1" }),
      episode({ id: "s2e2", number: 2, title: "S2E2" }),
    ],
  });
  const seasons = [specials, season1, season2];

  it("classifies a full non-special season as season", () => {
    expect(classifyWatchedSelection(["s1e1", "s1e2"], seasons)).toEqual({
      kind: "season",
      seasonNumber: 1,
    });
  });

  it("keeps a full season plus one special as per-episode", () => {
    expect(classifyWatchedSelection(["s1e1", "s1e2", "sp1"], seasons)).toEqual({
      kind: "episodes",
    });
  });

  it("classifies all non-special episodes as show", () => {
    expect(classifyWatchedSelection(["s1e1", "s1e2", "s2e1", "s2e2"], seasons)).toEqual({
      kind: "show",
    });
  });

  it("keeps a partial selection as per-episode", () => {
    expect(classifyWatchedSelection(["s1e1"], seasons)).toEqual({ kind: "episodes" });
  });
});

describe("buildTreeRows", () => {
  const s = season({
    id: "s1",
    number: 1,
    episodes: [episode({ id: "ep1", number: 1 }), episode({ id: "ep2", number: 2 })],
  });

  it("emits only season rows when nothing is expanded", () => {
    const rows = buildTreeRows({
      sortedSeasons: [s],
      expandedSeasons: new Set(),
      expandedEpisodes: new Set(),
      getEpisodeFiles: () => [],
      subtitleFilesByEpisode: {},
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "season", id: "s1", depth: 0, expanded: false });
  });

  it("emits episode rows under an expanded season", () => {
    const rows = buildTreeRows({
      sortedSeasons: [s],
      expandedSeasons: new Set(["s1"]),
      expandedEpisodes: new Set(),
      getEpisodeFiles: () => [],
      subtitleFilesByEpisode: {},
    });
    expect(rows.map((r) => r.kind)).toEqual(["season", "episode", "episode"]);
    expect(rows[0]).toMatchObject({ kind: "season", expanded: true });
  });

  it("emits file then subtitle rows under an expanded episode", () => {
    const rows = buildTreeRows({
      sortedSeasons: [s],
      expandedSeasons: new Set(["s1"]),
      expandedEpisodes: new Set(["ep1"]),
      getEpisodeFiles: (_seasonId, episodeId) =>
        episodeId === "ep1" ? [file({ id: "f1", episode_id: "ep1" })] : [],
      subtitleFilesByEpisode: { ep1: [sub({ file_name: "a.srt", language: "en" })] },
    });
    expect(rows.map((r) => r.kind)).toEqual(["season", "episode", "file", "subtitle", "episode"]);
    expect(rows[2]).toMatchObject({ kind: "file", id: "file:f1", depth: 2, episodeNumber: 1 });
    expect(rows[3]).toMatchObject({ kind: "subtitle", id: "ep1:sub:a.srt", depth: 2 });
  });
});

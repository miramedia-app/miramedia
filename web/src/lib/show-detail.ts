import type { components } from "@/lib/api/api";

export type Season = components["schemas"]["PublicSeason"];
export type Episode = Season["episodes"][number];
export type SubtitleFile = components["schemas"]["SubtitleFile"];
export type EpisodeFile = components["schemas"]["PublicEpisodeFile"];
export type RichTorrent = components["schemas"]["RichTorrent"];

/** One flattened row of the season → episode → file/subtitle tree. */
export type TreeRow =
  | { kind: "season"; id: string; depth: 0; data: Season; expanded: boolean }
  | {
      kind: "episode";
      id: string;
      depth: 1;
      data: Episode;
      seasonId: string;
      seasonNumber: number;
      expanded: boolean;
    }
  | {
      kind: "file";
      id: string;
      depth: 2;
      data: EpisodeFile;
      seasonId: string;
      seasonNumber: number;
      episodeId: string;
      episodeNumber: number;
      episodeTitle: string;
    }
  | {
      kind: "subtitle";
      id: string;
      depth: 2;
      data: SubtitleFile;
      seasonId: string;
      episodeId: string;
    };

/** Discriminated target for the delete-confirmation modal. */
export type DeleteTarget =
  | { type: "file"; fileId: string }
  | { type: "subtitle"; episodeId: string; fileName: string }
  | { type: "episode"; episodeId: string; seasonId: string }
  | { type: "season"; seasonId: string }
  | { type: "torrent"; torrentId: string; torrentName: string }
  | { type: "bulk-files" }
  | { type: "bulk-torrents" };

/** One season's shape needed to classify a watched-state bulk selection. */
export type ClassifiableSeason = {
  number: number;
  episodes: readonly { id: string }[];
};

export type WatchedSelectionClassification =
  | { kind: "season"; seasonNumber: number }
  | { kind: "show" }
  | { kind: "episodes" };

function sameIdSet(selected: ReadonlySet<string>, ids: readonly string[]): boolean {
  return selected.size === ids.length && ids.every((id) => selected.has(id));
}

/**
 * Decide whether a bulk watched toggle can use the coarse season/show endpoints.
 * Selections that include specials (season 0) always stay per-episode because
 * those endpoints hardcode `include_specials: false`.
 */
export function classifyWatchedSelection(
  selection: readonly string[],
  seasons: readonly ClassifiableSeason[],
): WatchedSelectionClassification {
  const selected = new Set(selection);
  if (selected.size === 0) return { kind: "episodes" };

  const includesSpecial = seasons.some(
    (season) => season.number === 0 && season.episodes.some((episode) => selected.has(episode.id)),
  );
  if (includesSpecial) return { kind: "episodes" };

  const nonSpecialSeasons = seasons.filter((season) => season.number !== 0);
  const allNonSpecialIds = nonSpecialSeasons.flatMap((season) =>
    season.episodes.map((episode) => episode.id),
  );
  if (allNonSpecialIds.length > 0 && sameIdSet(selected, allNonSpecialIds)) {
    return { kind: "show" };
  }

  const matchingSeason = nonSpecialSeasons.find(
    (season) =>
      season.episodes.length > 0 &&
      sameIdSet(
        selected,
        season.episodes.map((episode) => episode.id),
      ),
  );
  if (matchingSeason) {
    return { kind: "season", seasonNumber: matchingSeason.number };
  }

  return { kind: "episodes" };
}

export function fileKey(fileId: string) {
  return `file:${fileId}`;
}

export function subKey(episodeId: string, fileName: string) {
  return `${episodeId}:sub:${fileName}`;
}

/** Unique, sorted subtitle languages keyed by episode id. */
export function subtitleLanguagesByEpisode(
  subtitleFilesByEpisode: Record<string, SubtitleFile[]>,
): Record<string, string[]> {
  return Object.fromEntries(
    Object.entries(subtitleFilesByEpisode).map(([id, files]) => [
      id,
      [...new Set(files.map((f) => f.language))].sort(),
    ]),
  );
}

/** True when every downloaded episode in the season has at least one subtitle. */
export function seasonHasAllSubtitles(
  season: Season,
  subtitlesByEpisode: Record<string, string[]>,
): boolean {
  const downloaded = season.episodes.filter((ep) => ep.downloaded);
  if (downloaded.length === 0) return false;
  return downloaded.every((ep) => (subtitlesByEpisode[ep.id]?.length ?? 0) > 0);
}

export interface BuildTreeRowsArgs {
  sortedSeasons: Season[];
  expandedSeasons: Set<string>;
  expandedEpisodes: Set<string>;
  getEpisodeFiles: (seasonId: string, episodeId: string) => EpisodeFile[];
  subtitleFilesByEpisode: Record<string, SubtitleFile[]>;
}

/**
 * Flatten the season/episode/file/subtitle hierarchy into the row list the
 * DataListSection renders, honoring the current expansion state. Seasons and
 * episodes are assumed pre-sorted by the bundle query.
 */
export function buildTreeRows({
  sortedSeasons,
  expandedSeasons,
  expandedEpisodes,
  getEpisodeFiles,
  subtitleFilesByEpisode,
}: BuildTreeRowsArgs): TreeRow[] {
  const rows: TreeRow[] = [];
  for (const s of sortedSeasons) {
    rows.push({
      kind: "season",
      id: s.id,
      depth: 0,
      data: s,
      expanded: expandedSeasons.has(s.id),
    });
    if (!expandedSeasons.has(s.id)) continue;
    const eps = s.episodes;
    for (const ep of eps) {
      rows.push({
        kind: "episode",
        id: ep.id,
        depth: 1,
        data: ep,
        seasonId: s.id,
        seasonNumber: s.number,
        expanded: expandedEpisodes.has(ep.id),
      });
      if (!expandedEpisodes.has(ep.id)) continue;
      for (const f of getEpisodeFiles(s.id, ep.id)) {
        rows.push({
          kind: "file",
          id: fileKey(f.id!),
          depth: 2,
          data: f,
          seasonId: s.id,
          seasonNumber: s.number,
          episodeId: ep.id,
          episodeNumber: ep.number,
          episodeTitle: ep.title ?? "",
        });
      }
      for (const sub of subtitleFilesByEpisode[ep.id] ?? []) {
        rows.push({
          kind: "subtitle",
          id: subKey(ep.id, sub.file_name),
          depth: 2,
          data: sub,
          seasonId: s.id,
          episodeId: ep.id,
        });
      }
    }
  }
  return rows;
}

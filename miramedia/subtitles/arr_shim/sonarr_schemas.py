"""Pure Sonarr v3 JSON builders for the Bazarr compatibility shim."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from miramedia.file_status import ImportOutcome
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.subtitles.arr_shim import common
from miramedia.torrents.schemas import Quality

# Keys Bazarr's sonarr/sync/parser.py reads — keep tests in sync.
SERIES_PARSER_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "sortTitle",
        "path",
        "tvdbId",
        "imdbId",
        "year",
        "ended",
        "lastAired",
        "overview",
        "images",
        "alternateTitles",
        "tags",
        "seriesType",
        "monitored",
        "qualityProfileId",
        "languageProfileId",
        "originalLanguage",
    }
)

EPISODE_PARSER_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "seriesId",
        "title",
        "seasonNumber",
        "episodeNumber",
        "absoluteEpisodeNumber",
        "monitored",
        "hasFile",
        # Bazarr's episode sync reads this unconditionally (sonarr/sync/
        # episodes.py) — omitting it raised KeyError 'episodeFileId' and
        # aborted the whole sync, even though the nested episodeFile was there.
        "episodeFileId",
        "tvdbId",
    }
)

EPISODE_FILE_PARSER_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "path",
        "size",
        "sceneName",
        "language",
        "languages",
        "quality",
        "mediaInfo",
    }
)


def quality_to_sonarr(quality: Quality) -> tuple[str, int]:
    return common.quality_to_arr(quality)


def language_name_from_code(code: str | None) -> str:
    return common.language_name_from_code(code)


def show_tvdb_id(show: Show) -> int:
    # Some subtitle providers match by tvdbId; title/year still carry most matching.
    if show.metadata_provider != "tvdb":
        return 0
    try:
        return int(show.external_id)
    except (TypeError, ValueError):
        return 0


def last_aired_from_episodes(episodes: Iterable[Episode]) -> str | None:
    latest: date | None = None
    for episode in episodes:
        if episode.air_date is None:
            continue
        if latest is None or episode.air_date > latest:
            latest = episode.air_date
    return latest.isoformat() if latest is not None else None


def last_aired_from_show(show: Show) -> str | None:
    episodes = (episode for season in show.seasons for episode in season.episodes)
    return last_aired_from_episodes(episodes)


def imported_episode_files(episode: Episode) -> list[EpisodeFile]:
    return [
        episode_file
        for episode_file in episode.episode_files
        if episode_file.import_status == ImportOutcome.imported
    ]


def series_json(
    show: Show,
    *,
    arr_id: int,
    path: Path | str,
) -> dict[str, Any]:
    return {
        "id": arr_id,
        "title": show.name,
        "sortTitle": show.name.lower(),
        "path": str(path),
        "tvdbId": show_tvdb_id(show),
        "imdbId": show.imdb_id or "",
        "year": show.year or 0,
        "ended": show.ended,
        "lastAired": last_aired_from_show(show),
        "overview": show.overview,
        "images": [],
        "alternateTitles": [],
        "tags": [],
        "seriesType": "standard",
        "monitored": not show.skipped,
        "qualityProfileId": 1,
        "languageProfileId": 1,
        "originalLanguage": {
            "name": language_name_from_code(show.original_language),
        },
    }


def episode_file_json(
    episode_file: EpisodeFile,
    *,
    arr_id: int,
    path: Path | str,
    size: int,
) -> dict[str, Any]:
    quality_name, resolution = quality_to_sonarr(episode_file.quality)
    return {
        "id": arr_id,
        "path": str(path),
        "size": size,
        "sceneName": None,
        "language": dict(common.DEFAULT_AUDIO_LANGUAGE),
        "languages": [dict(common.DEFAULT_AUDIO_LANGUAGE)],
        "quality": {
            "quality": {
                "name": quality_name,
                "resolution": resolution,
            },
        },
        "mediaInfo": {
            "videoCodec": episode_file.codec or "",
            "audioCodec": "",
        },
    }


def episode_json(
    episode: Episode,
    *,
    arr_id: int,
    series_arr_id: int,
    season_number: int,
    include_episode_file: bool = False,
    episode_file: EpisodeFile | None = None,
    episode_file_arr_id: int | None = None,
    episode_file_path: Path | str | None = None,
    episode_file_size: int | None = None,
) -> dict[str, Any]:
    # Keep this predicate identical to ``_episode_file_payload``'s servability gate.
    has_servable_file = (
        episode_file_arr_id is not None
        and episode_file_path is not None
        and episode_file_size is not None
        and episode_file_size > 0
    )
    payload: dict[str, Any] = {
        "id": arr_id,
        "seriesId": series_arr_id,
        "title": episode.title,
        "seasonNumber": season_number,
        "episodeNumber": episode.number,
        "absoluteEpisodeNumber": None,
        "monitored": not episode.skipped,
        "hasFile": has_servable_file,
        "episodeFileId": episode_file_arr_id if has_servable_file else 0,
        "tvdbId": 0,
    }
    if (
        include_episode_file
        and has_servable_file
        and episode_file is not None
        and episode_file_arr_id is not None
        and episode_file_path is not None
        and episode_file_size is not None
    ):
        payload["episodeFile"] = episode_file_json(
            episode_file,
            arr_id=episode_file_arr_id,
            path=episode_file_path,
            size=episode_file_size,
        )
    return payload


def pick_primary_imported_file(episode: Episode) -> EpisodeFile | None:
    imported = imported_episode_files(episode)
    if not imported:
        return None
    return imported[0]


def iter_show_episodes(
    show: Show,
) -> Iterable[tuple[Season, Episode]]:
    for season in show.seasons:
        for episode in season.episodes:
            yield season, episode


def collect_entity_uuids(
    shows: Sequence[Show],
) -> tuple[list[UUID], list[UUID], list[UUID]]:
    series_uuids: list[UUID] = []
    episode_uuids: list[UUID] = []
    file_uuids: list[UUID] = []
    for show in shows:
        series_uuids.append(show.id)
        for season in show.seasons:
            for episode in season.episodes:
                episode_uuids.append(episode.id)
                file_uuids.extend(
                    episode_file.id for episode_file in imported_episode_files(episode)
                )
    return series_uuids, episode_uuids, file_uuids


def merge_arr_id_maps(
    *maps: Mapping[UUID, int],
) -> dict[UUID, int]:
    merged: dict[UUID, int] = {}
    for mapping in maps:
        merged.update(mapping)
    return merged

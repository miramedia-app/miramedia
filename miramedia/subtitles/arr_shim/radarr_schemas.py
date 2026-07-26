"""Pure Radarr v3 JSON builders for the Bazarr compatibility shim."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import Movie, MovieFile
from miramedia.subtitles.arr_shim import common

# Keys Bazarr's radarr/sync/parser.py reads — keep tests in sync.
MOVIE_PARSER_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "sortTitle",
        "overview",
        "year",
        "tmdbId",
        "imdbId",
        "monitored",
        "tags",
        "alternateTitles",
        "images",
        "qualityProfileId",
        "originalLanguage",
        "path",
        "hasFile",
        "movieFileId",
    }
)

MOVIE_FILE_PARSER_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "path",
        "size",
        "sceneName",
        "languages",
        "quality",
        "mediaInfo",
    }
)


def movie_tmdb_id(movie: Movie) -> int:
    if movie.metadata_provider != "tmdb":
        return 0
    try:
        return int(movie.external_id)
    except (TypeError, ValueError):
        return 0


def imported_movie_files(movie: Movie) -> list[MovieFile]:
    return [
        movie_file
        for movie_file in movie.movie_files
        if movie_file.import_status == ImportOutcome.imported
    ]


def movie_has_imported_file(movie: Movie) -> bool:
    return bool(imported_movie_files(movie))


def pick_best_imported_file(movie: Movie) -> MovieFile | None:
    imported = imported_movie_files(movie)
    if not imported:
        return None
    return min(imported, key=lambda movie_file: int(movie_file.quality))


def movie_file_json(
    movie_file: MovieFile,
    *,
    arr_id: int,
    path: Path | str,
    size: int,
) -> dict[str, Any]:
    quality_name, resolution = common.quality_to_arr(movie_file.quality)
    return {
        "id": arr_id,
        "path": str(path),
        "size": size,
        "sceneName": None,
        "languages": [dict(common.DEFAULT_AUDIO_LANGUAGE)],
        "quality": {
            "quality": {
                "name": quality_name,
                "resolution": resolution,
            },
        },
        "mediaInfo": common.media_info_payload(video_codec=movie_file.codec or ""),
    }


def movie_json(
    movie: Movie,
    *,
    arr_id: int,
    path: Path | str,
    movie_file: MovieFile | None = None,
    movie_file_arr_id: int | None = None,
    movie_file_path: Path | str | None = None,
    movie_file_size: int | None = None,
) -> dict[str, Any]:
    # Keep in sync with sonarr_schemas.episode_json: only advertise files we can describe.
    has_servable_file = (
        movie_has_imported_file(movie)
        and movie_file is not None
        and movie_file_arr_id is not None
        and movie_file_path is not None
        and movie_file_size is not None
        and movie_file_size > 0
    )
    payload: dict[str, Any] = {
        "id": arr_id,
        "title": movie.name,
        "sortTitle": movie.name.lower(),
        "overview": movie.overview,
        "year": movie.year or 0,
        "tmdbId": movie_tmdb_id(movie),
        "imdbId": movie.imdb_id or "",
        "monitored": not movie.skipped,
        "tags": [],
        "alternateTitles": [],
        "images": [],
        "qualityProfileId": 1,
        "originalLanguage": {
            "name": common.language_name_from_code(movie.original_language),
        },
        "path": str(path),
        "hasFile": has_servable_file,
        "movieFileId": movie_file_arr_id if has_servable_file else 0,
    }
    if (
        has_servable_file
        and movie_file is not None
        and movie_file_arr_id is not None
        and movie_file_path is not None
        and movie_file_size is not None
    ):
        payload["movieFile"] = movie_file_json(
            movie_file,
            arr_id=movie_file_arr_id,
            path=movie_file_path,
            size=movie_file_size,
        )
    return payload


def collect_entity_uuids(
    movies: Sequence[Movie],
) -> tuple[list[UUID], list[UUID]]:
    movie_uuids: list[UUID] = []
    file_uuids: list[UUID] = []
    for movie in movies:
        movie_uuids.append(movie.id)
        file_uuids.extend(movie_file.id for movie_file in imported_movie_files(movie))
    return movie_uuids, file_uuids


def merge_arr_id_maps(
    *maps: Mapping[UUID, int],
) -> dict[UUID, int]:
    merged: dict[UUID, int] = {}
    for mapping in maps:
        merged.update(mapping)
    return merged

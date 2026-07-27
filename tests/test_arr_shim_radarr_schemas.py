"""Unit tests for Radarr shim JSON schema builders."""

from __future__ import annotations

import uuid
from pathlib import Path

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import Movie, MovieFile
from miramedia.subtitles.arr_shim import common, radarr_schemas, shim_paths
from miramedia.torrents.schemas import Quality


def _movie(**overrides: object) -> Movie:
    movie = Movie(
        id=uuid.uuid4(),
        external_id="603",
        metadata_provider="tmdb",
        name="The Matrix",
        overview="Overview",
        year=1999,
        imdb_id="tt0133093",
        original_language="en",
        skipped=False,
    )
    movie.movie_files = []
    for key, value in overrides.items():
        setattr(movie, key, value)
    return movie


def _movie_file(**overrides: object) -> MovieFile:
    movie_file = MovieFile(
        id=uuid.uuid4(),
        movie_id=uuid.uuid4(),
        quality=Quality.fullhd,
        codec="h264",
        import_status=ImportOutcome.imported,
    )
    for key, value in overrides.items():
        setattr(movie_file, key, value)
    return movie_file


def test_quality_to_arr_maps_all_values() -> None:
    assert common.quality_to_arr(Quality.uhd) == ("WEBDL-2160p", 2160)
    assert common.quality_to_arr(Quality.fullhd) == ("WEBDL-1080p", 1080)
    assert common.quality_to_arr(Quality.hd) == ("WEBDL-720p", 720)
    assert common.quality_to_arr(Quality.sd) == ("SDTV", 480)
    assert common.quality_to_arr(Quality.unknown) == ("Unknown", 0)


def test_monitored_inverts_skipped() -> None:
    movie = _movie(skipped=True)
    payload = radarr_schemas.movie_json(movie, arr_id=1, path="/movies/matrix")
    assert payload["monitored"] is False


def test_has_file_only_when_imported() -> None:
    imported = _movie_file(import_status=ImportOutcome.imported)
    pending = _movie_file(import_status=ImportOutcome.pending)
    movie = _movie()
    movie.movie_files = [pending]
    payload = radarr_schemas.movie_json(movie, arr_id=1, path="/movies/matrix")
    assert payload["hasFile"] is False
    assert "movieFile" not in payload

    movie.movie_files = [pending, imported]
    payload = radarr_schemas.movie_json(
        movie,
        arr_id=1,
        path="/movies/matrix",
        movie_file=imported,
        movie_file_arr_id=9,
        movie_file_path="/movies/matrix/matrix.mkv",
        movie_file_size=50_000,
    )
    assert payload["hasFile"] is True
    assert payload["movieFile"]["id"] == 9


def test_movie_file_omitted_when_null_would_crash_bazarr() -> None:
    movie = _movie()
    movie.movie_files = []
    payload = radarr_schemas.movie_json(movie, arr_id=1, path="/movies/matrix")
    assert "movieFile" not in payload


def test_tmdb_id_provider_gating() -> None:
    movie = _movie(metadata_provider="tmdb", external_id="603")
    assert radarr_schemas.movie_tmdb_id(movie) == 603

    movie = _movie(metadata_provider="imdb", external_id="603")
    assert radarr_schemas.movie_tmdb_id(movie) == 0


def test_pick_best_imported_file_prefers_highest_quality() -> None:
    movie = _movie()
    low = _movie_file(quality=Quality.hd)
    high = _movie_file(quality=Quality.uhd)
    mid = _movie_file(quality=Quality.fullhd)
    movie.movie_files = [low, high, mid]
    assert radarr_schemas.pick_best_imported_file(movie) is high


def test_movie_json_contains_parser_keys() -> None:
    movie = _movie()
    payload = radarr_schemas.movie_json(movie, arr_id=1, path="/movies/matrix")
    assert set(payload) == radarr_schemas.MOVIE_PARSER_KEYS


def test_has_file_false_when_movie_file_path_missing() -> None:
    imported = _movie_file(import_status=ImportOutcome.imported)
    movie = _movie()
    movie.movie_files = [imported]
    payload = radarr_schemas.movie_json(
        movie,
        arr_id=1,
        path="/movies/matrix",
        movie_file=imported,
        movie_file_arr_id=9,
        movie_file_path=None,
        movie_file_size=50_000,
    )
    assert payload["hasFile"] is False
    assert payload["movieFileId"] == 0
    assert "movieFile" not in payload


def test_has_file_false_when_movie_file_size_zero() -> None:
    imported = _movie_file(import_status=ImportOutcome.imported)
    movie = _movie()
    movie.movie_files = [imported]
    payload = radarr_schemas.movie_json(
        movie,
        arr_id=1,
        path="/movies/matrix",
        movie_file=imported,
        movie_file_arr_id=9,
        movie_file_path="/movies/matrix/matrix.mkv",
        movie_file_size=0,
    )
    assert payload["hasFile"] is False
    assert payload["movieFileId"] == 0
    assert "movieFile" not in payload


def test_has_file_true_emits_movie_file_id_and_movie_file() -> None:
    imported = _movie_file(import_status=ImportOutcome.imported)
    movie = _movie()
    movie.movie_files = [imported]
    payload = radarr_schemas.movie_json(
        movie,
        arr_id=1,
        path="/movies/matrix",
        movie_file=imported,
        movie_file_arr_id=9,
        movie_file_path="/movies/matrix/matrix.mkv",
        movie_file_size=50_000,
    )
    assert payload["hasFile"] is True
    assert payload["movieFileId"] == 9
    assert payload["movieFile"]["id"] == 9


def test_rootfolder_accessible_when_path_missing() -> None:
    missing = Path("/nonexistent/rootfolder-test-path")
    payloads = shim_paths.rootfolder_payloads([missing])
    assert len(payloads) == 1
    assert payloads[0]["accessible"] is True
    assert payloads[0]["freeSpace"] == 0


def test_rootfolder_accessible_reports_free_space(tmp_path: Path) -> None:
    payloads = shim_paths.rootfolder_payloads([tmp_path])
    assert len(payloads) == 1
    assert payloads[0]["accessible"] is True
    assert payloads[0]["freeSpace"] > 0


def test_movie_file_json_contains_parser_keys_and_media_info() -> None:
    movie_file = _movie_file(codec="hevc")
    payload = radarr_schemas.movie_file_json(
        movie_file,
        arr_id=3,
        path="/movies/matrix/matrix.mkv",
        size=123456,
    )
    assert set(payload) == radarr_schemas.MOVIE_FILE_PARSER_KEYS
    assert set(payload["mediaInfo"]) == common.MEDIA_INFO_EMPTY_KEYS
    assert payload["quality"]["quality"]["name"] == "WEBDL-1080p"
    assert payload["mediaInfo"]["videoCodec"] == "hevc"

"""Endpoint tests for the Radarr Bazarr compatibility shim."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import Movie, MovieFile
from miramedia.subtitles.arr_shim import radarr_schemas, radarr_service, shim_paths
from miramedia.subtitles.config import BazarrConfig, SubtitleConfig
from miramedia.torrents.schemas import Quality


def _bazarr_config(**overrides: Any) -> MagicMock:
    bazarr = BazarrConfig(**overrides)
    subtitles = SubtitleConfig(bazarr=bazarr)
    config = MagicMock()
    config.subtitles = subtitles
    return config


@pytest.fixture
def shim_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.subtitles.arr_shim import auth as shim_auth

    async def _stub_session() -> Any:
        yield MagicMock()

    app.dependency_overrides[get_session] = _stub_session
    monkeypatch.setattr(
        shim_auth,
        "MiraMediaConfig",
        lambda: _bazarr_config(shim_api_key="test-shim-key"),
    )
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _sample_movie() -> Movie:
    movie_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    file_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    movie = Movie(
        id=movie_id,
        external_id="603",
        metadata_provider="tmdb",
        name="The Matrix",
        overview="A test movie",
        year=1999,
        imdb_id="tt0133093",
        original_language="en",
        skipped=False,
    )
    movie_file = MovieFile(
        id=file_id,
        movie_id=movie_id,
        quality=Quality.fullhd,
        codec="h264",
        import_status=ImportOutcome.imported,
    )
    movie.movie_files = [movie_file]
    return movie


def _movie_payload(movie: Movie, *, include_file: bool) -> dict[str, Any]:
    movie_file = radarr_schemas.pick_best_imported_file(movie)
    return radarr_schemas.movie_json(
        movie,
        arr_id=10,
        path="/data/movies/The Matrix (1999)",
        movie_file=movie_file if include_file else None,
        movie_file_arr_id=30 if include_file else None,
        movie_file_path="/data/movies/The Matrix (1999)/matrix.mkv"
        if include_file
        else None,
        movie_file_size=50_000 if include_file else None,
    )


def _mixed_library_movies() -> tuple[Movie, Movie]:
    resolvable_movie = _sample_movie()
    unresolvable_movie = _sample_movie()
    unresolvable_movie.id = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    unresolvable_movie.movie_files[0].id = uuid.UUID(
        "dddddddd-dddd-dddd-dddd-dddddddddddd"
    )
    unresolvable_movie.name = "Missing Movie"
    return resolvable_movie, unresolvable_movie


def _patch_list_movies_mixed_library(
    monkeypatch: pytest.MonkeyPatch,
    resolvable_movie: Movie,
    unresolvable_movie: Movie,
    *,
    batch_sizes: object | None = None,
) -> None:
    async def _load_all(_db: object) -> list[Movie]:
        return [resolvable_movie, unresolvable_movie]

    async def _batch_ids(
        _db: object, _movies: list[Movie]
    ) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
        return (
            {resolvable_movie.id: 10, unresolvable_movie.id: 11},
            {
                resolvable_movie.movie_files[0].id: 30,
                unresolvable_movie.movie_files[0].id: 31,
            },
        )

    def _batch_roots(_layout: object, _movies: list[Movie]) -> dict[uuid.UUID, Path]:
        return {
            resolvable_movie.id: Path("/data/movies/The Matrix (1999)"),
            unresolvable_movie.id: Path("/data/movies/Missing Movie (1999)"),
        }

    def _batch_file_paths(
        _layout: object, _movies: list[Movie]
    ) -> dict[uuid.UUID, Path | None]:
        return {
            resolvable_movie.movie_files[0].id: Path(
                "/data/movies/The Matrix (1999)/matrix.mkv"
            ),
            unresolvable_movie.movie_files[0].id: None,
        }

    async def _default_batch_sizes(
        _db: object, *, file_ids: list[uuid.UUID], paths: dict[uuid.UUID, Path | None]
    ) -> dict[uuid.UUID, int]:
        _ = file_ids, paths
        return {resolvable_movie.movie_files[0].id: 50_000}

    monkeypatch.setattr(radarr_service, "_load_all_movies", _load_all)
    monkeypatch.setattr(radarr_service, "_batch_arr_ids_for_movies", _batch_ids)
    monkeypatch.setattr(
        radarr_service.shim_paths, "batch_movie_root_paths", _batch_roots
    )
    monkeypatch.setattr(
        radarr_service.shim_paths,
        "batch_movie_file_paths_for_movies",
        _batch_file_paths,
    )
    monkeypatch.setattr(
        radarr_service.shim_paths,
        "batch_video_sizes",
        batch_sizes or _default_batch_sizes,
    )
    monkeypatch.setattr(
        radarr_service.IntegrityPathLayout, "from_config", lambda: object()
    )
    monkeypatch.setattr(
        radarr_service,
        "release_session_before_external_io",
        AsyncMock(),
    )


def _expected_mixed_library_payloads(
    resolvable_movie: Movie,
    unresolvable_movie: Movie,
) -> list[dict[str, Any]]:
    resolvable_primary = radarr_schemas.pick_best_imported_file(resolvable_movie)
    unresolvable_primary = radarr_schemas.pick_best_imported_file(unresolvable_movie)
    return [
        radarr_schemas.movie_json(
            resolvable_movie,
            arr_id=10,
            path="/data/movies/The Matrix (1999)",
            movie_file=resolvable_primary,
            movie_file_arr_id=30,
            movie_file_path="/data/movies/The Matrix (1999)/matrix.mkv",
            movie_file_size=50_000,
        ),
        radarr_schemas.movie_json(
            unresolvable_movie,
            arr_id=11,
            path="/data/movies/Missing Movie (1999)",
            movie_file=unresolvable_primary,
            movie_file_arr_id=31,
            movie_file_path=None,
            movie_file_size=None,
        ),
    ]


@pytest.fixture
def sample_movie() -> Movie:
    return _sample_movie()


def test_movie_list_contains_parser_keys(
    shim_client: TestClient,
    sample_movie: Movie,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        radarr_service,
        "list_movies",
        AsyncMock(return_value=[_movie_payload(sample_movie, include_file=True)]),
    )
    response = shim_client.get(
        "/radarr/api/v3/movie",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert set(body[0]) >= radarr_schemas.MOVIE_PARSER_KEYS
    assert "movieFile" in body[0]
    assert set(body[0]["movieFile"]) == radarr_schemas.MOVIE_FILE_PARSER_KEYS
    assert set(body[0]["movieFile"]["mediaInfo"]) == {
        "videoCodec",
        "videoCodecID",
        "videoCodecLibrary",
        "audioCodec",
        "audioCodecID",
        "audioProfile",
        "audioAdditionalFeatures",
        "audioLanguages",
    }


def test_movie_list_omits_movie_file_when_not_imported(
    shim_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_movie = _sample_movie()
    pending_movie.movie_files[0].import_status = ImportOutcome.pending
    monkeypatch.setattr(
        radarr_service,
        "list_movies",
        AsyncMock(return_value=[_movie_payload(pending_movie, include_file=True)]),
    )
    response = shim_client.get(
        "/radarr/api/v3/movie",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["hasFile"] is False
    assert "movieFile" not in body[0]


def test_unknown_movie_returns_404(
    shim_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    async def _raise(_db: object, movie_arr_id: int) -> dict[str, Any]:
        _ = movie_arr_id
        raise HTTPException(status_code=404)

    monkeypatch.setattr(radarr_service, "get_movie", _raise)
    response = shim_client.get(
        "/radarr/api/v3/movie/999",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 404


def test_history_returns_empty_list(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/radarr/api/v3/history",
        params={
            "apikey": "test-shim-key",
            "eventType": 1,
            "movieId": 20,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_rootfolder_accessible_true(
    shim_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        radarr_service,
        "list_rootfolders",
        lambda: [
            {
                "id": 1,
                "path": "/data/movies",
                "accessible": True,
                "freeSpace": 0,
            }
        ],
    )
    response = shim_client.get(
        "/radarr/api/v3/rootfolder",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["accessible"] is True


def test_tag_returns_empty_list(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/radarr/api/v3/tag",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_movie_requires_api_key(shim_client: TestClient) -> None:
    response = shim_client.get("/radarr/api/v3/movie")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_list_movies_marks_unresolvable_files_as_not_having_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolvable_movie, unresolvable_movie = _mixed_library_movies()
    _patch_list_movies_mixed_library(monkeypatch, resolvable_movie, unresolvable_movie)

    payloads = await radarr_service.list_movies(MagicMock())

    assert len(payloads) == 2
    by_id = {payload["id"]: payload for payload in payloads}
    assert by_id[10]["hasFile"] is True
    assert by_id[10]["movieFileId"] == 30
    assert "movieFile" in by_id[10]
    assert by_id[11]["hasFile"] is False
    assert by_id[11]["movieFileId"] == 0
    assert "movieFile" not in by_id[11]


@pytest.mark.anyio
async def test_list_movies_payload_shape_pinned_for_mixed_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolvable_movie, unresolvable_movie = _mixed_library_movies()
    _patch_list_movies_mixed_library(monkeypatch, resolvable_movie, unresolvable_movie)

    payloads = await radarr_service.list_movies(MagicMock())

    assert payloads == _expected_mixed_library_payloads(
        resolvable_movie, unresolvable_movie
    )


@pytest.mark.anyio
async def test_list_movies_batch_video_sizes_uses_primary_files_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolvable_movie, unresolvable_movie = _mixed_library_movies()
    secondary_file = MovieFile(
        id=uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        movie_id=resolvable_movie.id,
        quality=Quality.hd,
        codec="h264",
        import_status=ImportOutcome.imported,
    )
    resolvable_movie.movie_files.append(secondary_file)
    captured_file_ids: list[list[uuid.UUID]] = []

    def _batch_file_paths_with_secondary(
        _layout: object, _movies: list[Movie]
    ) -> dict[uuid.UUID, Path | None]:
        return {
            resolvable_movie.movie_files[0].id: Path(
                "/data/movies/The Matrix (1999)/matrix.mkv"
            ),
            secondary_file.id: Path("/data/movies/The Matrix (1999)/extras.mkv"),
            unresolvable_movie.movie_files[0].id: None,
        }

    async def _capturing_batch_sizes(
        _db: object, *, file_ids: list[uuid.UUID], paths: dict[uuid.UUID, Path | None]
    ) -> dict[uuid.UUID, int]:
        captured_file_ids.append(list(file_ids))
        _ = paths
        primary = radarr_schemas.pick_best_imported_file(resolvable_movie)
        assert primary is not None
        return {primary.id: 50_000}

    _patch_list_movies_mixed_library(
        monkeypatch,
        resolvable_movie,
        unresolvable_movie,
        batch_sizes=_capturing_batch_sizes,
    )
    monkeypatch.setattr(
        radarr_service.shim_paths,
        "batch_movie_file_paths_for_movies",
        _batch_file_paths_with_secondary,
    )

    await radarr_service.list_movies(MagicMock())

    assert captured_file_ids == [
        [
            resolvable_movie.movie_files[0].id,
            unresolvable_movie.movie_files[0].id,
        ]
    ]


@pytest.mark.anyio
async def test_list_movies_offloads_path_resolution_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolvable_movie, unresolvable_movie = _mixed_library_movies()
    _patch_list_movies_mixed_library(monkeypatch, resolvable_movie, unresolvable_movie)
    real_to_thread = asyncio.to_thread
    recorded_funcs: list[object] = []

    async def _spy_to_thread(
        func: object, /, *args: object, **kwargs: object
    ) -> object:
        recorded_funcs.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(radarr_service.asyncio, "to_thread", _spy_to_thread)

    await radarr_service.list_movies(MagicMock())

    assert shim_paths.batch_movie_root_paths in recorded_funcs
    assert shim_paths.batch_movie_file_paths_for_movies in recorded_funcs

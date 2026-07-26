"""Endpoint tests for the Sonarr Bazarr compatibility shim."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.file_status import ImportOutcome
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.subtitles.arr_shim import sonarr_schemas, sonarr_service
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


def _sample_show() -> Show:
    show_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    season_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    episode_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    file_id = uuid.UUID("44444444-4444-4444-4444-444444444444")

    show = Show(
        id=show_id,
        external_id="12345",
        metadata_provider="tvdb",
        name="Sample Show",
        overview="A test show",
        year=2020,
        ended=False,
        imdb_id="tt0000001",
        original_language="en",
        skipped=False,
    )
    season = Season(id=season_id, show_id=show_id, number=1)
    episode = Episode(
        id=episode_id,
        season_id=season_id,
        number=1,
        title="Pilot",
        air_date=date(2020, 1, 2),
        skipped=False,
    )
    episode_file = EpisodeFile(
        id=file_id,
        episode_id=episode_id,
        quality=Quality.fullhd,
        codec="h264",
        import_status=ImportOutcome.imported,
    )
    episode.episode_files = [episode_file]
    season.episodes = [episode]
    show.seasons = [season]
    return show


def _series_payload(show: Show) -> dict[str, Any]:
    return sonarr_schemas.series_json(
        show,
        arr_id=10,
        path="/data/shows/Sample Show",
    )


def _episode_payload(show: Show, *, include_file: bool) -> dict[str, Any]:
    season = show.seasons[0]
    episode = season.episodes[0]
    primary_file = sonarr_schemas.pick_primary_imported_file(episode)
    return sonarr_schemas.episode_json(
        episode,
        arr_id=20,
        series_arr_id=10,
        season_number=season.number,
        include_episode_file=include_file,
        episode_file=primary_file if include_file else None,
        episode_file_arr_id=(30 if include_file and primary_file is not None else None),
        episode_file_path=(
            "/data/shows/Sample Show/S01E01.mkv"
            if include_file and primary_file is not None
            else None
        ),
        episode_file_size=(
            50_000 if include_file and primary_file is not None else None
        ),
    )


def _episode_file_payload(show: Show) -> dict[str, Any]:
    episode_file = show.seasons[0].episodes[0].episode_files[0]
    return sonarr_schemas.episode_file_json(
        episode_file,
        arr_id=30,
        path="/data/shows/Sample Show/S01E01.mkv",
        size=50_000,
    )


@pytest.fixture
def sample_show() -> Show:
    return _sample_show()


def test_series_list_contains_parser_keys(
    shim_client: TestClient,
    sample_show: Show,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonarr_service,
        "list_series",
        AsyncMock(return_value=[_series_payload(sample_show)]),
    )
    response = shim_client.get(
        "/sonarr/api/v3/series",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert set(body[0]) == sonarr_schemas.SERIES_PARSER_KEYS


def test_episode_list_embeds_file_when_requested(
    shim_client: TestClient,
    sample_show: Show,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonarr_service,
        "list_episodes",
        AsyncMock(
            return_value=[
                _episode_payload(sample_show, include_file=True),
            ]
        ),
    )
    response = shim_client.get(
        "/sonarr/api/v3/episode",
        params={
            "apikey": "test-shim-key",
            "seriesId": 10,
            "includeEpisodeFile": "true",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["hasFile"] is True
    assert "episodeFile" in body[0]
    assert set(body[0]["episodeFile"]) == sonarr_schemas.EPISODE_FILE_PARSER_KEYS


def test_episode_list_omits_episode_file_when_not_imported(
    shim_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_show = _sample_show()
    pending_show.seasons[0].episodes[0].episode_files[
        0
    ].import_status = ImportOutcome.pending
    monkeypatch.setattr(
        sonarr_service,
        "list_episodes",
        AsyncMock(
            return_value=[
                _episode_payload(pending_show, include_file=True),
            ]
        ),
    )
    response = shim_client.get(
        "/sonarr/api/v3/episode",
        params={
            "apikey": "test-shim-key",
            "seriesId": 10,
            "includeEpisodeFile": "true",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["hasFile"] is False
    assert "episodeFile" not in body[0]


def test_unknown_series_returns_404(
    shim_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    async def _raise(
        _db: object,
        *,
        series_arr_id: int,
        include_episode_file: bool,
    ) -> list[dict[str, Any]]:
        _ = series_arr_id, include_episode_file
        raise HTTPException(status_code=404)

    monkeypatch.setattr(sonarr_service, "list_episodes", _raise)
    response = shim_client.get(
        "/sonarr/api/v3/episode",
        params={"apikey": "test-shim-key", "seriesId": 999},
    )
    assert response.status_code == 404


def test_history_returns_empty_list(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/history",
        params={
            "apikey": "test-shim-key",
            "eventType": 1,
            "episodeId": 20,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_rootfolder_accessible_true(
    shim_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonarr_service,
        "list_rootfolders",
        lambda: [
            {
                "id": 1,
                "path": "/data/shows",
                "accessible": True,
                "freeSpace": 0,
            }
        ],
    )
    response = shim_client.get(
        "/sonarr/api/v3/rootfolder",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["accessible"] is True


def test_tag_returns_empty_list(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/tag",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_series_requires_api_key(shim_client: TestClient) -> None:
    response = shim_client.get("/sonarr/api/v3/series")
    assert response.status_code == 401


def test_episode_file_list_only_imported_shape(
    shim_client: TestClient,
    sample_show: Show,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sonarr_service,
        "list_episode_files",
        AsyncMock(return_value=[_episode_file_payload(sample_show)]),
    )
    response = shim_client.get(
        "/sonarr/api/v3/episodeFile",
        params={"apikey": "test-shim-key", "seriesId": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body[0]) == sonarr_schemas.EPISODE_FILE_PARSER_KEYS
    assert body[0]["size"] > 0


# --- Regressions found against a live Bazarr container ---


def test_show_root_path_accepts_orm_rows() -> None:
    """`IntegrityPathLayout` takes the pydantic schemas, not ORM rows.

    The shim loads ORM objects, so every path helper must convert first —
    passing the ORM row straight through raised ``TypeError: expected Show``
    and turned `GET /sonarr/api/v3/series` into a 500 as soon as the library
    was non-empty.
    """
    from miramedia.subtitles.arr_shim import shim_paths
    from miramedia.torrents.integrity import IntegrityPathLayout

    show = _sample_show()
    # Column defaults only materialise on flush; a DB-loaded row always has
    # them, so fill them in to match what the shim actually receives.
    show.library = "Default"
    show.wanted_episode_count = 1
    show.downloaded_episode_count = 1
    show.list_progress_status = "none"
    show.continuous_download = False
    show.downloaded = True
    show.poster_path = ""
    show.rating = ""
    show.status = ""
    show.season_count = 1
    season = show.seasons[0]
    season.skipped = False
    episode_file = season.episodes[0].episode_files[0]
    episode_file.hdr = False
    episode_file.source = ""
    episode_file.variant = ""
    episode_file.extra = ""
    episode_file.attempt_count = 1
    layout = IntegrityPathLayout(
        _show_libraries=[],
        _movie_libraries=[],
        _default_show_directory=Path("/shows"),
        _default_movie_directory=Path("/movies"),
    )

    path = shim_paths.show_root_path(layout, show)
    assert str(path).startswith("/shows/")
    assert shim_paths.batch_show_root_paths(layout, [show]) == {show.id: path}


def test_episode_payload_carries_episode_file_id() -> None:
    """Bazarr's episode sync reads `episodeFileId` unconditionally."""
    show = _sample_show()
    episode = show.seasons[0].episodes[0]
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=2,
        series_arr_id=17,
        season_number=1,
        include_episode_file=True,
        episode_file=episode.episode_files[0],
        episode_file_arr_id=99,
        episode_file_path=Path("/shows/Sample Show/Season 1/ep.mkv"),
        episode_file_size=123,
    )
    assert payload["episodeFileId"] == 99
    assert "episodeFileId" in sonarr_schemas.EPISODE_PARSER_KEYS


# --- Single-entity shim endpoints (plan 137) ---


def _partial_episode_tree() -> tuple[Episode, Show, EpisodeFile]:
    """Episode tree matching the shim repo's partial eager-load (no ``show.seasons``)."""
    show_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    season_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    episode_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    file_id = uuid.UUID("44444444-4444-4444-4444-444444444444")

    show = Show(
        id=show_id,
        external_id="12345",
        metadata_provider="tvdb",
        name="Sample Show",
        overview="A test show",
        year=2020,
        ended=False,
        imdb_id="tt0000001",
        original_language="en",
        skipped=False,
        library="Default",
    )
    season = Season(id=season_id, show_id=show_id, number=1)
    episode = Episode(
        id=episode_id,
        season_id=season_id,
        number=1,
        title="Pilot",
        air_date=date(2020, 1, 2),
        skipped=False,
    )
    episode_file = EpisodeFile(
        id=file_id,
        episode_id=episode_id,
        quality=Quality.fullhd,
        codec="h264",
        import_status=ImportOutcome.imported,
    )
    episode.episode_files = [episode_file]
    episode_file.episode = episode
    season.show = show
    episode.season = season
    # show.seasons intentionally unset — partial eager-load shape
    return episode, show, episode_file


def _stub_arr_id_response(
    entity_type: str,
    _uuids: list[uuid.UUID],
    show: Show,
    episode: Episode,
    episode_file: EpisodeFile,
) -> dict[uuid.UUID, int]:
    if entity_type == "series":
        return {show.id: 10}
    if entity_type == "episode":
        return {episode.id: 20}
    return {episode_file.id: 30}


_FORBIDDEN_SHOW_CONTEXT_MSG = "_resolve_show_context must not be called"
_UNLOADED_SEASONS_MSG = "show.seasons is not loaded"


def _forbidden_resolve_show_context(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(_FORBIDDEN_SHOW_CONTEXT_MSG)


def _raising_show_seasons(_self: Show) -> list[Season]:
    from sqlalchemy.exc import MissingGreenlet

    raise MissingGreenlet(_UNLOADED_SEASONS_MSG)


@pytest.mark.anyio
async def test_get_episode_without_loaded_show_seasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode, show, episode_file = _partial_episode_tree()
    db = MagicMock()

    async def _resolve_episode_uuid(_db: object, episode_arr_id: int) -> uuid.UUID:
        assert episode_arr_id == 20
        return episode.id

    async def _get_episode_with_show_tree(
        _self: object, episode_id: uuid.UUID
    ) -> Episode:
        assert episode_id == episode.id
        return episode

    async def _tracking_get_or_create(
        _db: object,
        entity_type: str,
        uuids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        return _stub_arr_id_response(entity_type, uuids, show, episode, episode_file)

    async def _batch_video_sizes(
        *_args: object,
        **_kwargs: object,
    ) -> dict[uuid.UUID, int]:
        return {episode_file.id: 50_000}

    monkeypatch.setattr(
        sonarr_service, "_resolve_show_context", _forbidden_resolve_show_context
    )
    monkeypatch.setattr(sonarr_service, "_resolve_episode_uuid", _resolve_episode_uuid)
    monkeypatch.setattr(
        sonarr_service.ShowRepository,
        "get_episode_with_show_tree",
        _get_episode_with_show_tree,
    )
    monkeypatch.setattr(
        sonarr_service,
        "get_or_create_arr_ids",
        _tracking_get_or_create,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_video_sizes",
        _batch_video_sizes,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_episode_file_paths_for_episode",
        lambda *_args, **_kwargs: {
            episode_file.id: Path("/data/shows/Sample Show/S01E01.mkv")
        },
    )

    monkeypatch.setattr(Show, "seasons", property(_raising_show_seasons))

    payload = await sonarr_service.get_episode(db, 20)
    assert payload["id"] == 20
    assert payload["hasFile"] is True
    assert payload["episodeFileId"] == 30


@pytest.mark.anyio
async def test_get_episode_file_without_loaded_show_seasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode, show, episode_file = _partial_episode_tree()
    db = MagicMock()

    async def _resolve_episode_file_uuid(_db: object, file_arr_id: int) -> uuid.UUID:
        assert file_arr_id == 30
        return episode_file.id

    async def _get_episode_file_with_show_tree(
        _self: object, file_id: uuid.UUID
    ) -> EpisodeFile:
        assert file_id == episode_file.id
        return episode_file

    async def _tracking_get_or_create(
        _db: object,
        entity_type: str,
        uuids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        return _stub_arr_id_response(entity_type, uuids, show, episode, episode_file)

    async def _batch_video_sizes(
        *_args: object,
        **_kwargs: object,
    ) -> dict[uuid.UUID, int]:
        return {episode_file.id: 50_000}

    monkeypatch.setattr(
        sonarr_service, "_resolve_show_context", _forbidden_resolve_show_context
    )
    monkeypatch.setattr(
        sonarr_service, "_resolve_episode_file_uuid", _resolve_episode_file_uuid
    )
    monkeypatch.setattr(
        sonarr_service.ShowRepository,
        "get_episode_file_with_show_tree",
        _get_episode_file_with_show_tree,
    )
    monkeypatch.setattr(
        sonarr_service,
        "get_or_create_arr_ids",
        _tracking_get_or_create,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_video_sizes",
        _batch_video_sizes,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_episode_file_paths_for_episode",
        lambda *_args, **_kwargs: {
            episode_file.id: Path("/data/shows/Sample Show/S01E01.mkv")
        },
    )

    monkeypatch.setattr(Show, "seasons", property(_raising_show_seasons))

    payload = await sonarr_service.get_episode_file(db, 30)
    assert payload["id"] == 30
    assert payload["size"] == 50_000


@pytest.mark.anyio
async def test_get_episode_allocates_arr_ids_for_one_episode_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode, show, episode_file = _partial_episode_tree()
    db = MagicMock()
    recorded: list[tuple[str, list[uuid.UUID]]] = []

    async def _resolve_episode_uuid(_db: object, _episode_arr_id: int) -> uuid.UUID:
        return episode.id

    async def _get_episode_with_show_tree(
        _self: object, _episode_id: uuid.UUID
    ) -> Episode:
        return episode

    async def _tracking_get_or_create(
        _db: object,
        entity_type: str,
        uuids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        recorded.append((entity_type, list(uuids)))
        if entity_type == "series":
            return {show.id: 10}
        if entity_type == "episode":
            return {episode.id: 20}
        return {episode_file.id: 30}

    async def _batch_video_sizes(
        *_args: object,
        **_kwargs: object,
    ) -> dict[uuid.UUID, int]:
        return {episode_file.id: 50_000}

    monkeypatch.setattr(sonarr_service, "_resolve_episode_uuid", _resolve_episode_uuid)
    monkeypatch.setattr(
        sonarr_service.ShowRepository,
        "get_episode_with_show_tree",
        _get_episode_with_show_tree,
    )
    monkeypatch.setattr(
        sonarr_service,
        "get_or_create_arr_ids",
        _tracking_get_or_create,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_video_sizes",
        _batch_video_sizes,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_episode_file_paths_for_episode",
        lambda *_args, **_kwargs: {
            episode_file.id: Path("/data/shows/Sample Show/S01E01.mkv")
        },
    )

    await sonarr_service.get_episode(db, 20)

    episode_calls = [uuids for kind, uuids in recorded if kind == "episode"]
    assert episode_calls == [[episode.id]]
    file_calls = [uuids for kind, uuids in recorded if kind == "episode_file"]
    assert file_calls == [[episode_file.id]]


@pytest.mark.anyio
async def test_get_episode_payload_matches_fully_loaded_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show = _sample_show()
    season = show.seasons[0]
    episode = season.episodes[0]
    episode_file = episode.episode_files[0]
    db = MagicMock()

    async def _resolve_episode_uuid(_db: object, _episode_arr_id: int) -> uuid.UUID:
        return episode.id

    async def _get_episode_with_show_tree(
        _self: object, _episode_id: uuid.UUID
    ) -> Episode:
        return episode

    async def _tracking_get_or_create(
        _db: object,
        entity_type: str,
        _uuids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        if entity_type == "series":
            return {show.id: 10}
        if entity_type == "episode":
            return {episode.id: 20}
        return {episode_file.id: 30}

    async def _batch_video_sizes(
        *_args: object,
        **_kwargs: object,
    ) -> dict[uuid.UUID, int]:
        return {episode_file.id: 50_000}

    monkeypatch.setattr(sonarr_service, "_resolve_episode_uuid", _resolve_episode_uuid)
    monkeypatch.setattr(
        sonarr_service.ShowRepository,
        "get_episode_with_show_tree",
        _get_episode_with_show_tree,
    )
    monkeypatch.setattr(
        sonarr_service,
        "get_or_create_arr_ids",
        _tracking_get_or_create,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_video_sizes",
        _batch_video_sizes,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_episode_file_paths_for_episode",
        lambda *_args, **_kwargs: {
            episode_file.id: Path("/data/shows/Sample Show/S01E01.mkv")
        },
    )

    payload = await sonarr_service.get_episode(db, 20, include_episode_file=True)

    expected = {
        "id": 20,
        "seriesId": 10,
        "title": "Pilot",
        "seasonNumber": 1,
        "episodeNumber": 1,
        "absoluteEpisodeNumber": None,
        "monitored": True,
        "hasFile": True,
        "episodeFileId": 30,
        "tvdbId": 0,
        "episodeFile": {
            "id": 30,
            "path": "/data/shows/Sample Show/S01E01.mkv",
            "size": 50_000,
            "sceneName": None,
            "language": {"name": "English"},
            "languages": [{"name": "English"}],
            "quality": {
                "quality": {
                    "name": "WEBDL-1080p",
                    "resolution": 1080,
                },
            },
            "mediaInfo": {
                "videoCodec": "h264",
                "audioCodec": "",
            },
        },
    }
    assert payload == expected


# --- hasFile / episodeFileId servability consistency (plan 139) ---


def _unresolvable_episode_context(
    show: Show,
    episode: Episode,
    episode_file: EpisodeFile,
) -> sonarr_service._ResolvedShowContext:
    return sonarr_service._ResolvedShowContext(
        show=show,
        series_arr_id=10,
        show_path="/data/shows/Sample Show",
        episode_arr_ids={episode.id: 20},
        episode_file_arr_ids={episode_file.id: 30},
        episode_file_paths={},
        episode_file_sizes={},
    )


@pytest.mark.anyio
async def test_get_episode_has_file_false_when_path_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode, show, episode_file = _partial_episode_tree()
    db = MagicMock()

    async def _resolve_episode_uuid(_db: object, episode_arr_id: int) -> uuid.UUID:
        assert episode_arr_id == 20
        return episode.id

    async def _get_episode_with_show_tree(
        _self: object, episode_id: uuid.UUID
    ) -> Episode:
        assert episode_id == episode.id
        return episode

    async def _tracking_get_or_create(
        _db: object,
        entity_type: str,
        uuids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        return _stub_arr_id_response(entity_type, uuids, show, episode, episode_file)

    async def _batch_video_sizes(
        *_args: object,
        **_kwargs: object,
    ) -> dict[uuid.UUID, int]:
        return {}

    monkeypatch.setattr(sonarr_service, "_resolve_episode_uuid", _resolve_episode_uuid)
    monkeypatch.setattr(
        sonarr_service.ShowRepository,
        "get_episode_with_show_tree",
        _get_episode_with_show_tree,
    )
    monkeypatch.setattr(
        sonarr_service,
        "get_or_create_arr_ids",
        _tracking_get_or_create,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_video_sizes",
        _batch_video_sizes,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_episode_file_paths_for_episode",
        lambda *_args, **_kwargs: {episode_file.id: None},
    )

    payload = await sonarr_service.get_episode(db, 20)
    assert payload["hasFile"] is False
    assert payload["episodeFileId"] == 0


@pytest.mark.anyio
async def test_list_episodes_has_file_false_when_path_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show = _sample_show()
    episode = show.seasons[0].episodes[0]
    episode_file = episode.episode_files[0]
    db = MagicMock()
    ctx = _unresolvable_episode_context(show, episode, episode_file)

    async def _resolve_show_uuid(_db: object, _series_arr_id: int) -> uuid.UUID:
        assert _series_arr_id == 10
        return show.id

    async def _load_show_by_uuid(_db: object, show_uuid: uuid.UUID) -> Show:
        assert show_uuid == show.id
        return show

    async def _resolve_show_context(
        _db: object,
        _show: Show,
        **kwargs: object,
    ) -> sonarr_service._ResolvedShowContext:
        _ = kwargs
        return ctx

    monkeypatch.setattr(sonarr_service, "_resolve_show_uuid", _resolve_show_uuid)
    monkeypatch.setattr(sonarr_service, "_load_show_by_uuid", _load_show_by_uuid)
    monkeypatch.setattr(sonarr_service, "_resolve_show_context", _resolve_show_context)

    episodes = await sonarr_service.list_episodes(
        db,
        series_arr_id=10,
        include_episode_file=False,
    )
    assert len(episodes) == 1
    assert episodes[0]["hasFile"] is False
    assert episodes[0]["episodeFileId"] == 0


@pytest.mark.anyio
async def test_list_episode_files_omits_unresolvable_imported_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show = _sample_show()
    episode = show.seasons[0].episodes[0]
    episode_file = episode.episode_files[0]
    db = MagicMock()
    ctx = _unresolvable_episode_context(show, episode, episode_file)

    async def _resolve_show_uuid(_db: object, _series_arr_id: int) -> uuid.UUID:
        return show.id

    async def _load_show_by_uuid(_db: object, _show_uuid: uuid.UUID) -> Show:
        return show

    async def _resolve_show_context(
        _db: object,
        _show: Show,
        **kwargs: object,
    ) -> sonarr_service._ResolvedShowContext:
        _ = kwargs
        return ctx

    monkeypatch.setattr(sonarr_service, "_resolve_show_uuid", _resolve_show_uuid)
    monkeypatch.setattr(sonarr_service, "_load_show_by_uuid", _load_show_by_uuid)
    monkeypatch.setattr(sonarr_service, "_resolve_show_context", _resolve_show_context)

    files = await sonarr_service.list_episode_files(db, series_arr_id=10)
    assert files == []


@pytest.mark.anyio
async def test_get_episode_has_file_true_when_file_is_servable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode, show, episode_file = _partial_episode_tree()
    db = MagicMock()

    async def _resolve_episode_uuid(_db: object, _episode_arr_id: int) -> uuid.UUID:
        return episode.id

    async def _get_episode_with_show_tree(
        _self: object, _episode_id: uuid.UUID
    ) -> Episode:
        return episode

    async def _tracking_get_or_create(
        _db: object,
        entity_type: str,
        uuids: list[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        return _stub_arr_id_response(entity_type, uuids, show, episode, episode_file)

    async def _batch_video_sizes(
        *_args: object,
        **_kwargs: object,
    ) -> dict[uuid.UUID, int]:
        return {episode_file.id: 50_000}

    monkeypatch.setattr(sonarr_service, "_resolve_episode_uuid", _resolve_episode_uuid)
    monkeypatch.setattr(
        sonarr_service.ShowRepository,
        "get_episode_with_show_tree",
        _get_episode_with_show_tree,
    )
    monkeypatch.setattr(
        sonarr_service,
        "get_or_create_arr_ids",
        _tracking_get_or_create,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_video_sizes",
        _batch_video_sizes,
    )
    monkeypatch.setattr(
        "miramedia.subtitles.arr_shim.shim_paths.batch_episode_file_paths_for_episode",
        lambda *_args, **_kwargs: {
            episode_file.id: Path("/data/shows/Sample Show/S01E01.mkv")
        },
    )

    payload = await sonarr_service.get_episode(db, 20)
    assert payload["hasFile"] is True
    assert payload["episodeFileId"] == 30

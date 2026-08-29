"""Characterization tests for torrent router SSE search and manual flows."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid

os.environ.setdefault("MIRAMEDIA_LOG_FILE", "/dev/null")
from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.shows.schemas import (
    Episode,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.router import search_torrents_stream
from miramedia.torrents.schemas import (
    ImportProgress,
    ManualMapTargetType,
    MediaType,
    Quality,
)
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
    make_movie,
    make_torrent,
)
from tests.fakes.services import (
    build_movie_service,
    build_show_service,
    build_torrent_service,
)

MANUAL_DOWNLOAD_PREFIX = "/api/v1/torrents/manual/download"
TORRENTS_PREFIX = "/api/v1/torrents"


def _show_with_episodes(*, episode_count: int = 1) -> tuple[Show, list[Episode]]:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episodes = [
        Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(index + 1),
            title=f"Episode {index + 1}",
        )
        for index in range(episode_count)
    ]
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=episodes,
    )
    show = Show(
        id=show_id,
        name="Fixture Show",
        overview="",
        year=2024,
        external_id="fixture-show",
        metadata_provider="native",
        seasons=[season],
    )
    return show, episodes


def _result(title: str, *, url_suffix: str | None = None) -> IndexerQueryResult:
    suffix = url_suffix or title
    return IndexerQueryResult(
        title=title,
        download_url=f"magnet:?xt=urn:btih:{suffix}",
        seeders=50,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="fixture",
    )


def _show() -> Show:
    return Show(
        id=ShowId(uuid.uuid4()),
        name="Fixture Show",
        year=2024,
        library="/tv",
        overview="",
        external_id="fixture-show",
        metadata_provider="native",
        preferred_quality=["1080p (Full HD)"],
    )


@pytest.mark.anyio
async def test_sse_stream_yields_chunks_in_provider_completion_order() -> None:
    show = _show()
    first = _result("Fixture Show S01E01 1080p WEB-DL x264", url_suffix="first")
    second = _result("Fixture Show S01E02 1080p WEB-DL x264", url_suffix="second")

    class ShowRepository:
        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class ShowService:
        show_repository = ShowRepository()

        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class IndexerService:
        async def search(self, query, is_tv, on_partial):  # noqa: ARG002
            on_partial("provider-a", [first])
            await asyncio.sleep(0)
            on_partial("provider-b", [second])
            await asyncio.sleep(0)
            return [first, second]

    @asynccontextmanager
    async def background_session():
        yield object()

    save_calls: list[list[IndexerQueryResult]] = []

    async def save_results(_self, results):
        save_calls.append(list(results))

    with (
        patch("miramedia.database.background_session", background_session),
        patch(
            "miramedia.indexers.repository.IndexerRepository.save_results",
            save_results,
        ),
    ):
        response = await search_torrents_stream(
            request=object(),
            indexer_service=IndexerService(),
            media_type=MediaType.show,
            media_id=show.id,
            show_service=ShowService(),
            movie_service=object(),
            query_override="Fixture Show",
        )
        events = [event async for event in response.body_iterator]

    result_events = [event for event in events if event.event == "results"]
    assert [event.event for event in events] == ["results", "results", "done"]
    assert [json.loads(event.data)["source"] for event in result_events] == [
        "provider-a",
        "provider-b",
    ]
    assert len(save_calls) == 2
    assert save_calls[0][0].download_url.endswith("first")
    assert save_calls[1][0].download_url.endswith("second")


@pytest.mark.anyio
async def test_sse_stream_persists_each_provider_chunk_before_yield() -> None:
    show = _show()
    chunk = _result("Fixture Show S01E01 1080p WEB-DL x264")

    class ShowRepository:
        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class ShowService:
        show_repository = ShowRepository()

        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class IndexerService:
        async def search(self, query, is_tv, on_partial):  # noqa: ARG002
            on_partial("fixture", [chunk])
            await asyncio.sleep(0)
            return [chunk]

    persist_order: list[str] = []

    @asynccontextmanager
    async def background_session():
        persist_order.append("session-open")
        try:
            yield object()
        finally:
            persist_order.append("session-close")

    async def save_results(_self, results):
        persist_order.append("persist")
        return results

    with (
        patch("miramedia.database.background_session", background_session),
        patch(
            "miramedia.indexers.repository.IndexerRepository.save_results",
            save_results,
        ),
    ):
        response = await search_torrents_stream(
            request=object(),
            indexer_service=IndexerService(),
            media_type=MediaType.show,
            media_id=show.id,
            show_service=ShowService(),
            movie_service=object(),
            query_override="Fixture Show",
        )
        events = [event async for event in response.body_iterator]

    assert events[-1].event == "done"
    assert persist_order == ["session-open", "persist", "session-close"]


@pytest.mark.anyio
async def test_sse_stream_disconnect_cancels_search_and_sets_abort() -> None:
    show = _show()
    captured_abort: list[threading.Event] = []
    search_tasks: list[asyncio.Task] = []

    class TrackingEvent(threading.Event):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured_abort.append(self)

    class ShowRepository:
        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class ShowService:
        show_repository = ShowRepository()

        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class IndexerService:
        async def search(self, query, is_tv, on_partial):  # noqa: ARG002
            on_partial(
                "early-provider", [_result("Fixture Show S01E01 1080p WEB-DL x264")]
            )
            await asyncio.sleep(1)
            on_partial(
                "late-provider", [_result("Fixture Show S01E02 1080p WEB-DL x264")]
            )
            return []

    @asynccontextmanager
    async def background_session():
        yield object()

    async def save_results(_self, results):
        return results

    orig_create_task = asyncio.create_task

    def tracking_create_task(coro, **kwargs):
        task = orig_create_task(coro, **kwargs)
        search_tasks.append(task)
        return task

    with (
        patch("miramedia.database.background_session", background_session),
        patch(
            "miramedia.indexers.repository.IndexerRepository.save_results",
            save_results,
        ),
        patch("miramedia.torrents.search_stream.threading.Event", TrackingEvent),
        patch("asyncio.create_task", side_effect=tracking_create_task),
    ):
        response = await search_torrents_stream(
            request=object(),
            indexer_service=IndexerService(),
            media_type=MediaType.show,
            media_id=show.id,
            show_service=ShowService(),
            movie_service=object(),
            query_override="Fixture Show",
        )
        first = await anext(response.body_iterator)
        assert first.event == "results"
        await response.body_iterator.aclose()
        await asyncio.sleep(0.05)

    assert captured_abort[0].is_set()
    assert search_tasks[0].cancelled()


@pytest.mark.anyio
async def test_sse_partial_callback_ignores_results_after_abort() -> None:
    from miramedia.torrents.search_stream import TorrentSearchStreamOrchestrator

    show = _show()
    abort = threading.Event()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    orchestrator = TorrentSearchStreamOrchestrator(
        indexer_service=MagicMock(),
        media_obj=show,
        media_type=MediaType.show,
        is_tv=True,
        season_number=None,
        episode_number=None,
        query_override="Fixture Show",
        quality=None,
        codec=None,
        abort=abort,
    )
    on_partial = orchestrator.make_partial_callback(loop, queue.put_nowait)

    on_partial("before-abort", [_result("Fixture Show S01E01 1080p WEB-DL x264")])
    await asyncio.sleep(0)
    assert queue.qsize() == 1

    abort.set()
    on_partial("after-abort", [_result("Fixture Show S01E02 1080p WEB-DL x264")])
    await asyncio.sleep(0)
    assert queue.qsize() == 1


@contextmanager
def manual_download_client(
    *,
    torrent_service,
    indexer_service,
    show_service,
    movie_service,
    show_repository,
    movie_repository,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.indexers.dependencies import get_indexer_service
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.torrents.dependencies import get_torrent_service

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        user.is_active = True
        user.is_verified = True
        return user

    async def _superuser() -> Any:
        return await _active_user()

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_torrent_service] = lambda: torrent_service
    app.dependency_overrides[get_indexer_service] = lambda: indexer_service
    app.dependency_overrides[get_show_service] = lambda: show_service
    app.dependency_overrides[get_movie_service] = lambda: movie_service
    app.dependency_overrides[get_show_repository] = lambda: show_repository
    app.dependency_overrides[get_movie_repository] = lambda: movie_repository
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_manual_download_consumes_token_persists_result_and_links() -> None:
    movie = make_movie(name="Manual Download Movie")
    movie_repo = FakeMovieRepository()
    movie_repo.add_movie(movie)
    torrent_repo = FakeTorrentRepository(movie_repo=movie_repo)
    show_service, show_repo, _ = build_show_service(torrent_repo=torrent_repo)
    movie_service, movie_repo, _ = build_movie_service(
        movie_repo=movie_repo, torrent_repo=torrent_repo
    )
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    token = uuid.uuid4()
    payload = {
        "id": str(uuid.uuid4()),
        "title": "Manual.Release.1080p.WEB-DL.x264",
        "download_url": "magnet:?xt=urn:btih:manual",
        "seeders": 0,
        "flags": [],
        "size": 0,
        "usenet": False,
        "age": 0,
        "score": 0,
        "indexer": "manual",
    }
    torrent_repo.manual_parse_tokens[token] = payload

    linked = make_torrent(title="Manual.Release.1080p.WEB-DL.x264")
    torrent_service.download_and_link = AsyncMock(return_value=linked)
    torrent_service.compute_import_progress = AsyncMock(
        return_value=ImportProgress(total=1, imported=0, pending=1)
    )

    indexer_service = MagicMock()
    indexer_service.save_result = AsyncMock()

    body = {
        "download_token": str(token),
        "media_type": MediaType.movie.value,
        "media_id": str(movie.id),
        "quality_override": Quality.fullhd.value,
    }

    with manual_download_client(
        torrent_service=torrent_service,
        indexer_service=indexer_service,
        show_service=show_service,
        movie_service=movie_service,
        show_repository=show_repo,
        movie_repository=movie_repo,
    ) as client:
        response = client.post(MANUAL_DOWNLOAD_PREFIX, json=body)

    assert response.status_code == 200, response.text
    assert token not in torrent_repo.manual_parse_tokens
    indexer_service.save_result.assert_awaited_once()
    torrent_service.download_and_link.assert_awaited_once()


def test_manual_download_missing_token_returns_404() -> None:
    movie_repo = FakeMovieRepository()
    torrent_repo = FakeTorrentRepository(movie_repo=movie_repo)
    show_service, show_repo, _ = build_show_service(torrent_repo=torrent_repo)
    movie_service, movie_repo, _ = build_movie_service(
        movie_repo=movie_repo, torrent_repo=torrent_repo
    )
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    indexer_service = MagicMock()
    indexer_service.save_result = AsyncMock()

    body = {
        "download_token": str(uuid.uuid4()),
        "media_type": MediaType.movie.value,
        "media_id": str(uuid.uuid4()),
    }

    with manual_download_client(
        torrent_service=torrent_service,
        indexer_service=indexer_service,
        show_service=show_service,
        movie_service=movie_service,
        show_repository=show_repo,
        movie_repository=movie_repo,
    ) as client:
        response = client.post(MANUAL_DOWNLOAD_PREFIX, json=body)

    assert response.status_code == 404
    indexer_service.save_result.assert_not_awaited()


def test_manual_download_applies_library_override_before_link() -> None:
    movie = make_movie(name="Library Override Movie")
    movie.library = "Default"
    movie_repo = FakeMovieRepository()
    movie_repo.add_movie(movie)
    torrent_repo = FakeTorrentRepository(movie_repo=movie_repo)
    show_service, show_repo, _ = build_show_service(torrent_repo=torrent_repo)
    movie_service, movie_repo, _ = build_movie_service(
        movie_repo=movie_repo, torrent_repo=torrent_repo
    )
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    token = uuid.uuid4()
    torrent_repo.manual_parse_tokens[token] = {
        "id": str(uuid.uuid4()),
        "title": "Library.Override.1080p.WEB-DL.x264",
        "download_url": "magnet:?xt=urn:btih:library",
        "seeders": 0,
        "flags": [],
        "size": 0,
        "usenet": False,
        "age": 0,
        "score": 0,
        "indexer": "manual",
    }

    linked = make_torrent(title="Library.Override.1080p.WEB-DL.x264")
    torrent_service.download_and_link = AsyncMock(return_value=linked)
    torrent_service.compute_import_progress = AsyncMock(
        return_value=ImportProgress(total=1, imported=0, pending=1)
    )
    movie_service.set_movie_library = AsyncMock()
    indexer_service = MagicMock()
    indexer_service.save_result = AsyncMock()

    with (
        patch(
            "miramedia.config.MiraMediaConfig",
            return_value=MagicMock(
                misc=MagicMock(movie_libraries=[type("Lib", (), {"name": "Movies"})()])
            ),
        ),
        manual_download_client(
            torrent_service=torrent_service,
            indexer_service=indexer_service,
            show_service=show_service,
            movie_service=movie_service,
            show_repository=show_repo,
            movie_repository=movie_repo,
        ) as client,
    ):
        response = client.post(
            MANUAL_DOWNLOAD_PREFIX,
            json={
                "download_token": str(token),
                "media_type": MediaType.movie.value,
                "media_id": str(movie.id),
                "library": "Movies",
            },
        )

    assert response.status_code == 200, response.text
    movie_service.set_movie_library.assert_awaited_once()


def test_manual_map_path_escape_records_partial_failure(tmp_path) -> None:
    from miramedia.file_status import ImportOutcome

    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Manual.Map.Escape")
    torrent_repo.torrents[torrent.id] = torrent

    good_file = tmp_path / "good.mkv"
    good_file.write_bytes(b"video")
    show_service.import_episode_from_file = AsyncMock(
        return_value=(ImportOutcome.imported, None)
    )

    items = [
        {
            "relative_path": "../escape.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(episodes[0].id),
            "quality_override": Quality.fullhd.value,
        },
        {
            "relative_path": "good.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(episodes[0].id),
            "quality_override": Quality.fullhd.value,
        },
    ]

    from tests.test_bulk_torrent_batch_lookups import torrent_bulk_client

    with patch(
        "miramedia.torrents.paths.get_torrent_filepath",
        return_value=tmp_path,
    ):
        with torrent_bulk_client(
            torrent_repo=torrent_repo,
            show_service=show_service,
            movie_service=movie_service,
            torrent_service=torrent_service,
            torrent=torrent,
        ) as client:
            response = client.post(
                f"/api/v1/torrents/{torrent.id}/map",
                json={"items": items},
            )

    body = response.json()
    assert response.status_code == 200
    assert body["mapped"] == 1
    assert body["failed"] == 1
    assert body["errors"][0] == "path escapes torrent root: ../escape.mkv"

"""ID-only library queries: scan reconciliation must not load full show/movie trees."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import miramedia.imports.tasks as tasks
from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import ShowId
from tests.fakes.services import build_movie_service, build_show_service, run_async


def test_get_all_show_ids_skips_full_tree_loader() -> None:
    show_id = ShowId(uuid.uuid4())
    show_repo = MagicMock()
    show_repo.get_show_ids = AsyncMock(return_value=[show_id])
    show_repo.get_shows = AsyncMock()

    svc, _, _ = build_show_service(show_repo)

    ids = run_async(svc.get_all_show_ids())

    assert ids == [show_id]
    show_repo.get_show_ids.assert_awaited_once()
    show_repo.get_shows.assert_not_called()


def test_get_all_movie_ids_skips_full_row_load() -> None:
    movie_id = MovieId(uuid.uuid4())
    movie_repo = MagicMock()
    movie_repo.get_movie_ids = AsyncMock(return_value=[movie_id])
    movie_repo.get_movies = AsyncMock()

    svc, _, _ = build_movie_service(movie_repo)

    ids = run_async(svc.get_all_movie_ids())

    assert ids == [movie_id]
    movie_repo.get_movie_ids.assert_awaited_once()
    movie_repo.get_movies.assert_not_called()


def test_scan_reconciliation_uses_id_methods_not_full_loaders() -> None:
    show_id = ShowId(uuid.uuid4())
    movie_id = MovieId(uuid.uuid4())

    @asynccontextmanager
    async def mock_bg_session():
        yield MagicMock()

    @asynccontextmanager
    async def mock_show_service():
        service = MagicMock()
        service.get_all_show_ids = AsyncMock(return_value=[show_id])
        service.get_all_shows = AsyncMock(
            side_effect=AssertionError(
                "get_all_shows must not run during scan reconciliation"
            )
        )
        yield service

    @asynccontextmanager
    async def mock_movie_service():
        service = MagicMock()
        service.get_all_movie_ids = AsyncMock(return_value=[movie_id])
        service.get_all_movies = AsyncMock(
            side_effect=AssertionError(
                "get_all_movies must not run during scan reconciliation"
            )
        )
        yield service

    mock_repo = MagicMock()
    mock_repo.set_scan_run = AsyncMock()
    mock_repo.list_ignored_paths = AsyncMock(return_value=[])
    mock_repo.list_terminal_scan_cache = AsyncMock(return_value=[])
    mock_repo.replace_scan_cache = AsyncMock()

    mock_config = MagicMock()
    mock_config.imports.auto_import_on_scan = False

    scan_response = MagicMock(items=[])

    async def run() -> None:
        with (
            patch("miramedia.config.MiraMediaConfig", return_value=mock_config),
            patch("miramedia.database.background_session", mock_bg_session),
            patch("miramedia.database.bg_show_service", mock_show_service),
            patch("miramedia.database.bg_movie_service", mock_movie_service),
            patch(
                "miramedia.imports.repository.ImportsRepository",
                return_value=mock_repo,
            ),
            patch(
                "miramedia.imports.scan.scan_libraries",
                AsyncMock(return_value=scan_response),
            ),
        ):
            await tasks._scan_and_cache_body()

    asyncio.run(run())

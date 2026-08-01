"""Service-level tests for auto-download sweep candidate selection.

Uses fake repositories so no live DB is required.
"""

import asyncio
import types
import uuid
from unittest.mock import AsyncMock, patch

from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import ShowId


def _call(coro):
    return asyncio.run(coro)


def _show_rows():
    enabled = ShowId(uuid.uuid4())
    skipped = ShowId(uuid.uuid4())
    global_default = ShowId(uuid.uuid4())
    explicit_off = ShowId(uuid.uuid4())
    return [
        (enabled, False, True),
        (skipped, True, True),
        (global_default, False, None),
        (explicit_off, False, False),
    ]


def _movie_rows():
    enabled = MovieId(uuid.uuid4())
    skipped = MovieId(uuid.uuid4())
    global_default = MovieId(uuid.uuid4())
    explicit_off = MovieId(uuid.uuid4())
    return [
        (enabled, False, True),
        (skipped, True, True),
        (global_default, False, None),
        (explicit_off, False, False),
    ]


class TestAutoDownloadShowCandidates:
    def test_selects_non_skipped_continuous_download_shows(self):
        rows = _show_rows()
        enabled, skipped, global_default, explicit_off = (r[0] for r in rows)
        fake_repo = types.SimpleNamespace(
            get_show_auto_download_candidate_flags=AsyncMock(return_value=rows),
        )
        fake_svc = types.SimpleNamespace(show_repository=fake_repo)
        captured: list[ShowId] = []

        async def fake_try(show_id: ShowId, _max_downloads: int) -> None:
            captured.append(show_id)

        async def run() -> None:
            with (
                patch(
                    "miramedia.database.bg_show_service",
                ) as mock_bg,
                patch(
                    "miramedia.shows.service._try_auto_download_show_id_impl",
                    fake_try,
                ),
                patch(
                    "miramedia.media_service.MiraMediaConfig",
                ) as mock_config,
            ):
                mock_bg.return_value.__aenter__ = AsyncMock(return_value=fake_svc)
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_config.return_value.misc.continuous_download = True
                from miramedia.shows.service import _auto_download_missing_episodes_impl

                await _auto_download_missing_episodes_impl()

        _call(run())
        assert captured == [enabled, global_default]
        assert skipped not in captured
        assert explicit_off not in captured

    def test_honors_global_default_off(self):
        rows = _show_rows()
        enabled, _, global_default, _ = (r[0] for r in rows)
        fake_repo = types.SimpleNamespace(
            get_show_auto_download_candidate_flags=AsyncMock(return_value=rows),
        )
        fake_svc = types.SimpleNamespace(show_repository=fake_repo)
        captured: list[ShowId] = []

        async def fake_try(show_id: ShowId, _max_downloads: int) -> None:
            captured.append(show_id)

        async def run() -> None:
            with (
                patch(
                    "miramedia.database.bg_show_service",
                ) as mock_bg,
                patch(
                    "miramedia.shows.service._try_auto_download_show_id_impl",
                    fake_try,
                ),
                patch(
                    "miramedia.media_service.MiraMediaConfig",
                ) as mock_config,
            ):
                mock_bg.return_value.__aenter__ = AsyncMock(return_value=fake_svc)
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_config.return_value.misc.continuous_download = False
                from miramedia.shows.service import _auto_download_missing_episodes_impl

                await _auto_download_missing_episodes_impl()

        _call(run())
        assert captured == [enabled]
        assert global_default not in captured


class TestAutoDownloadMovieCandidates:
    def test_selects_non_skipped_continuous_download_movies(self):
        rows = _movie_rows()
        enabled, skipped, global_default, explicit_off = (r[0] for r in rows)
        fake_repo = types.SimpleNamespace(
            get_movie_auto_download_candidate_flags=AsyncMock(return_value=rows),
        )
        fake_svc = types.SimpleNamespace(movie_repository=fake_repo)
        captured: list[MovieId] = []

        async def fake_try(movie_id: MovieId) -> None:
            captured.append(movie_id)

        async def run() -> None:
            with (
                patch(
                    "miramedia.database.bg_movie_service",
                ) as mock_bg,
                patch(
                    "miramedia.movies.service._try_auto_download_movie_id_impl",
                    fake_try,
                ),
                patch(
                    "miramedia.media_service.MiraMediaConfig",
                ) as mock_config,
            ):
                mock_bg.return_value.__aenter__ = AsyncMock(return_value=fake_svc)
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_config.return_value.misc.continuous_download = True
                from miramedia.movies.service import _auto_download_missing_movies_impl

                await _auto_download_missing_movies_impl()

        _call(run())
        assert captured == [enabled, global_default]
        assert skipped not in captured
        assert explicit_off not in captured

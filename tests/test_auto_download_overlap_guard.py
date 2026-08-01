"""Tests for per-media auto-download overlap guard (Guard B)."""

import asyncio
import types
import uuid
from unittest.mock import AsyncMock, patch

from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import ShowId


def _call(coro):
    return asyncio.run(coro)


def _fake_show(show_id: ShowId):
    return types.SimpleNamespace(id=show_id, name="Test Show")


def _fake_movie(movie_id: MovieId):
    return types.SimpleNamespace(
        id=movie_id,
        name="Test Movie",
        skipped=False,
        continuous_download=None,
        release_date=None,
        auto_download_backoff_until=None,
        year=2024,
    )


class TestShowAutoDownloadOverlapGuard:
    def test_same_id_skips_overlapping_run(self):
        show_id = ShowId(uuid.uuid4())
        entries: list[ShowId] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_download(show, _max_downloads):
            entries.append(show.id)
            entered.set()
            await release.wait()

        fake_repo = types.SimpleNamespace(
            get_show_by_id=AsyncMock(return_value=_fake_show(show_id)),
        )
        fake_svc = types.SimpleNamespace(show_repository=fake_repo)

        async def run() -> None:
            with (
                patch("miramedia.database.bg_show_service") as mock_bg,
                patch(
                    "miramedia.shows.service._auto_download_for_show_impl",
                    slow_download,
                ),
            ):
                mock_bg.return_value.__aenter__ = AsyncMock(return_value=fake_svc)
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                from miramedia.shows.service import _try_auto_download_show_id_impl

                first = asyncio.create_task(_try_auto_download_show_id_impl(show_id))
                await entered.wait()
                second = asyncio.create_task(_try_auto_download_show_id_impl(show_id))
                await asyncio.sleep(0)
                assert entries == [show_id]
                release.set()
                await asyncio.gather(first, second)

        _call(run())

    def test_different_ids_run_concurrently(self):
        show_a = ShowId(uuid.uuid4())
        show_b = ShowId(uuid.uuid4())
        entries: list[ShowId] = []

        async def track(show, _max_downloads):
            entries.append(show.id)

        def make_svc(sid: ShowId):
            fake_repo = types.SimpleNamespace(
                get_show_by_id=AsyncMock(return_value=_fake_show(sid)),
            )
            return types.SimpleNamespace(show_repository=fake_repo)

        async def run() -> None:
            with (
                patch("miramedia.database.bg_show_service") as mock_bg,
                patch(
                    "miramedia.shows.service._auto_download_for_show_impl",
                    track,
                ),
            ):
                mock_bg.return_value.__aenter__ = AsyncMock(
                    side_effect=[make_svc(show_a), make_svc(show_b)]
                )
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                from miramedia.shows.service import _try_auto_download_show_id_impl

                await asyncio.gather(
                    _try_auto_download_show_id_impl(show_a),
                    _try_auto_download_show_id_impl(show_b),
                )

        _call(run())
        assert set(entries) == {show_a, show_b}


class TestMovieAutoDownloadOverlapGuard:
    def test_same_id_skips_overlapping_run(self):
        async def run() -> None:
            movie_id = MovieId(uuid.uuid4())
            entries: list[MovieId] = []
            entered = asyncio.Event()
            release = asyncio.Event()

            async def slow_search(*, movie):
                entries.append(movie.id)
                entered.set()
                await release.wait()
                return []

            fake_repo = types.SimpleNamespace(
                get_movie_by_id=AsyncMock(return_value=_fake_movie(movie_id)),
                get_movie_files_by_movie_id=AsyncMock(return_value=[]),
                db=AsyncMock(),
            )
            fake_svc = types.SimpleNamespace(
                movie_repository=fake_repo,
                is_movie_downloaded=AsyncMock(return_value=False),
                get_all_available_torrents_for_movie=slow_search,
                torrent_service=types.SimpleNamespace(
                    filter_deny_listed=AsyncMock(return_value=[]),
                ),
            )

            with patch("miramedia.database.bg_movie_service") as mock_bg:
                mock_bg.return_value.__aenter__ = AsyncMock(return_value=fake_svc)
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                from miramedia.movies.service import _try_auto_download_movie_id_impl

                first = asyncio.create_task(_try_auto_download_movie_id_impl(movie_id))
                await entered.wait()
                second = asyncio.create_task(_try_auto_download_movie_id_impl(movie_id))
                await asyncio.sleep(0)
                assert entries == [movie_id]
                release.set()
                await asyncio.gather(first, second)

        _call(run())

    def test_different_ids_run_concurrently(self):
        async def run() -> None:
            movie_a = MovieId(uuid.uuid4())
            movie_b = MovieId(uuid.uuid4())
            entries: list[MovieId] = []

            async def track(*, movie):
                entries.append(movie.id)
                return []

            def make_svc(mid: MovieId):
                fake_repo = types.SimpleNamespace(
                    get_movie_by_id=AsyncMock(return_value=_fake_movie(mid)),
                    get_movie_files_by_movie_id=AsyncMock(return_value=[]),
                    db=AsyncMock(),
                )
                return types.SimpleNamespace(
                    movie_repository=fake_repo,
                    is_movie_downloaded=AsyncMock(return_value=False),
                    get_all_available_torrents_for_movie=track,
                    torrent_service=types.SimpleNamespace(
                        filter_deny_listed=AsyncMock(return_value=[]),
                    ),
                )

            with patch("miramedia.database.bg_movie_service") as mock_bg:
                mock_bg.return_value.__aenter__ = AsyncMock(
                    side_effect=[make_svc(movie_a), make_svc(movie_b)]
                )
                mock_bg.return_value.__aexit__ = AsyncMock(return_value=False)
                from miramedia.movies.service import _try_auto_download_movie_id_impl

                await asyncio.gather(
                    _try_auto_download_movie_id_impl(movie_a),
                    _try_auto_download_movie_id_impl(movie_b),
                )

            assert set(entries) == {movie_a, movie_b}

        _call(run())

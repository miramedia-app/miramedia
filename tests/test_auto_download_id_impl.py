"""Characterization tests for per-id auto-download entry points (show + movie)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from miramedia.indexers.schemas import IndexerQueryResult
from tests.fakes import build_movie_service, build_show_service, run_async
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    make_movie,
    make_show,
)


def _indexer_result(title: str = "Test.Release.1080p") -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=f"magnet:?xt=urn:btih:{title}",
        seeders=10,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="x",
    )


def _backoff_hours() -> int:
    return 12


class TestShowAutoDownloadIdImpl:
    def _bg_show(self, svc):
        @asynccontextmanager
        async def fake_bg():
            yield svc

        return patch("miramedia.background_services.bg_show_service", fake_bg)

    def _common_show_patches(self, svc):
        return (
            patch.object(
                svc,
                "get_all_available_torrents_for_an_episode",
                AsyncMock(return_value=[_indexer_result()]),
            ),
            patch.object(
                svc,
                "get_all_available_torrents_for_a_season",
                AsyncMock(return_value=[]),
            ),
            patch.object(svc, "_episode_downloaded_from_cache", return_value=False),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
            patch.object(
                svc.torrent_service,
                "filter_deny_listed",
                AsyncMock(side_effect=lambda results: results),
            ),
            patch.object(svc, "_scan_season_video_files", return_value=[]),
        )

    def test_skipped_show_short_circuits(self) -> None:
        yesterday = datetime.now(UTC).astimezone().date() - timedelta(days=1)
        show = make_show(air_date=yesterday, skipped=True)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)
        searched: list[bool] = []

        async def track(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            self._bg_show(svc),
            patch.object(svc, "get_all_available_torrents_for_an_episode", track),
            patch.object(svc, "get_all_available_torrents_for_a_season", track),
        ):
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        assert searched == []

    def test_continuous_download_explicit_off_skips(self) -> None:
        yesterday = datetime.now(UTC).astimezone().date() - timedelta(days=1)
        show = make_show(air_date=yesterday, continuous_download=False)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)
        searched: list[bool] = []

        async def track(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            self._bg_show(svc),
            patch.object(svc, "get_all_available_torrents_for_an_episode", track),
        ):
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        assert searched == []

    def test_continuous_download_global_off_skips_when_unset(self) -> None:
        yesterday = datetime.now(UTC).astimezone().date() - timedelta(days=1)
        show = make_show(air_date=yesterday, continuous_download=None)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)
        searched: list[bool] = []

        async def track(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            self._bg_show(svc),
            patch.object(svc, "get_all_available_torrents_for_an_episode", track),
            patch("miramedia.media_service.MiraMediaConfig") as mock_config,
        ):
            mock_config.return_value.misc.continuous_download = False
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        assert searched == []

    def test_future_episode_not_searched(self) -> None:
        tomorrow = datetime.now(UTC).astimezone().date() + timedelta(days=1)
        show = make_show(air_date=tomorrow)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)
        searched: list[bool] = []

        async def track(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            self._bg_show(svc),
            patch.object(svc, "_episode_downloaded_from_cache", return_value=False),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
            patch.object(svc, "_scan_season_video_files", return_value=[]),
            patch.object(svc, "get_all_available_torrents_for_an_episode", track),
        ):
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        assert searched == []

    def test_active_backoff_skips_without_indexer_call(self) -> None:
        yesterday = datetime.now(UTC).astimezone().date() - timedelta(days=1)
        until = datetime.now(UTC) + timedelta(hours=6)
        show = make_show(
            air_date=yesterday,
            auto_download_backoff_until=until,
        )
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)
        searched: list[bool] = []

        async def track(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            self._bg_show(svc),
            patch.object(svc, "get_all_available_torrents_for_an_episode", track),
        ):
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        assert searched == []

    def test_deny_listed_results_write_backoff_horizon(self) -> None:
        yesterday = datetime.now(UTC).astimezone().date() - timedelta(days=1)
        show = make_show(air_date=yesterday)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)
        before = datetime.now(UTC)

        with (
            self._bg_show(svc),
            patch.object(
                svc,
                "get_all_available_torrents_for_an_episode",
                AsyncMock(return_value=[_indexer_result()]),
            ),
            patch.object(
                svc,
                "get_all_available_torrents_for_a_season",
                AsyncMock(return_value=[]),
            ),
            patch.object(svc, "_episode_downloaded_from_cache", return_value=False),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
            patch.object(
                svc.torrent_service, "filter_deny_listed", AsyncMock(return_value=[])
            ),
            patch.object(svc, "_scan_season_video_files", return_value=[]),
            patch("miramedia.media_service.MiraMediaConfig") as mock_config,
        ):
            mock_config.return_value.misc.auto_download_interval_hours = 1
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        updated = show_repo.shows[show.id]
        assert updated.auto_download_backoff_until is not None
        delta = updated.auto_download_backoff_until - before
        assert delta >= timedelta(hours=_backoff_hours() - 1)
        assert delta <= timedelta(hours=_backoff_hours() + 1)

    def test_successful_fan_out_invokes_download(self) -> None:
        yesterday = datetime.now(UTC).astimezone().date() - timedelta(days=1)
        show = make_show(air_date=yesterday)
        picked = _indexer_result()
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        with (
            self._bg_show(svc),
            patch.object(
                svc,
                "get_all_available_torrents_for_an_episode",
                AsyncMock(return_value=[_indexer_result()]),
            ),
            patch.object(
                svc,
                "get_all_available_torrents_for_a_season",
                AsyncMock(return_value=[]),
            ),
            patch.object(svc, "_episode_downloaded_from_cache", return_value=False),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
            patch.object(
                svc.torrent_service,
                "filter_deny_listed",
                AsyncMock(side_effect=lambda results: results),
            ),
            patch.object(svc, "_scan_season_video_files", return_value=[]),
            patch.object(
                svc, "_auto_download_first_valid", AsyncMock(return_value=picked)
            ) as mock_download,
        ):
            from miramedia.shows.service import _try_auto_download_show_id_impl

            run_async(_try_auto_download_show_id_impl(show.id))

        mock_download.assert_awaited_once()


class TestMovieAutoDownloadIdImpl:
    def _bg_movie(self, svc):
        @asynccontextmanager
        async def fake_bg():
            yield svc

        return patch("miramedia.background_services.bg_movie_service", fake_bg)

    def test_skipped_movie_short_circuits(self) -> None:
        movie = make_movie(skipped=True)
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_continuous_download_explicit_off_skips(self) -> None:
        movie = make_movie(continuous_download=False)
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_future_release_date_not_searched(self) -> None:
        tomorrow = datetime.now(UTC).astimezone().date() + timedelta(days=1)
        movie = make_movie(release_date=tomorrow)
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_active_backoff_skips_without_indexer_call(self) -> None:
        until = datetime.now(UTC) + timedelta(hours=6)
        movie = make_movie(auto_download_backoff_until=until)
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_deny_listed_results_write_backoff_horizon(self) -> None:
        movie = make_movie()
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        before = datetime.now(UTC)

        with (
            self._bg_movie(svc),
            patch.object(
                svc,
                "get_all_available_torrents_for_movie",
                AsyncMock(return_value=[_indexer_result()]),
            ),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=False)),
            patch.object(
                svc.torrent_service, "filter_deny_listed", AsyncMock(return_value=[])
            ),
            patch("miramedia.media_service.MiraMediaConfig") as mock_config,
        ):
            mock_config.return_value.misc.auto_download_interval_hours = 1
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        updated = movie_repo.movies[movie.id]
        assert updated.auto_download_backoff_until is not None
        delta = updated.auto_download_backoff_until - before
        assert delta >= timedelta(hours=_backoff_hours() - 1)
        assert delta <= timedelta(hours=_backoff_hours() + 1)

    def test_successful_fan_out_invokes_download(self) -> None:
        movie = make_movie()
        picked = _indexer_result()
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)

        with (
            self._bg_movie(svc),
            patch.object(
                svc,
                "get_all_available_torrents_for_movie",
                AsyncMock(return_value=[picked]),
            ),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=False)),
            patch.object(
                svc.torrent_service,
                "filter_deny_listed",
                AsyncMock(side_effect=lambda results: results),
            ),
            patch.object(
                svc, "_try_download_first_valid", AsyncMock(return_value=picked)
            ) as mock_download,
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        mock_download.assert_awaited_once()

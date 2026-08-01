"""Characterization tests for auto-download selection gates beyond sweep candidates."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
)
from miramedia.torrents.schemas import TorrentStatus
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import (
    FakeShowRepository,
    make_show,
    make_torrent,
)


class TestAutoDownloadGates:
    def test_skips_show_with_active_unimported_download(self) -> None:
        show = make_show()
        torrent = make_torrent()
        torrent = torrent.model_copy(update={"status": TorrentStatus.downloading})

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        show_repo.torrents_by_show[show.id] = [torrent]

        svc, _, torrent_repo = build_show_service(show_repo=show_repo)
        torrent_repo.torrents[torrent.id] = torrent

        searched: list = []

        async def fake_search(*_args, **_kwargs):
            searched.append(True)
            return []

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={torrent.id: False}),
            ),
            patch.object(svc, "is_episode_downloaded", AsyncMock(return_value=False)),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == []

    def test_skips_episode_with_pending_file_row(self) -> None:
        show = make_show()
        episode = show.seasons[0].episodes[0]
        episode.episode_files = [
            EpisodeFile(
                id=uuid.uuid4(),
                episode_id=episode.id,
                quality=2,
                torrent_id=None,
                import_status=ImportOutcome.pending,
            )
        ]

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list = []

        async def fake_search(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(svc, "is_episode_downloaded", AsyncMock(return_value=False)),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == []

    def test_future_episode_not_searched(self) -> None:
        tomorrow = datetime.now().astimezone().date() + timedelta(days=1)
        show = make_show(air_date=tomorrow)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list = []

        async def fake_search(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(svc, "is_episode_downloaded", AsyncMock(return_value=False)),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == []

    def test_wholly_undated_later_season_not_searched(self) -> None:
        yesterday = datetime.now().astimezone().date() - timedelta(days=1)
        show = make_show(air_date=yesterday)
        show.seasons.append(
            Season(
                id=SeasonId(uuid.uuid4()),
                show_id=show.id,
                number=SeasonNumber(2),
                episodes=[
                    Episode(
                        number=EpisodeNumber(1),
                        title="TBA",
                        air_date=None,
                    )
                ],
            )
        )
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list[tuple[int | None, int | None]] = []

        async def fake_search(*_args, **kwargs):
            searched.append((kwargs.get("season_number"), kwargs.get("episode_number")))
            return []

        async def is_downloaded(*, episode, **_kwargs):
            return episode.air_date is not None

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(svc, "get_all_available_torrents_for_an_episode", fake_search),
            patch.object(svc, "is_episode_downloaded", is_downloaded),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == []

    def test_undated_first_season_remains_searchable(self) -> None:
        show = make_show(air_date=None)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list[tuple[int | None, int | None]] = []

        async def fake_search(*_args, **kwargs):
            searched.append((kwargs.get("season_number"), kwargs.get("episode_number")))
            return []

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(svc, "get_all_available_torrents_for_an_episode", fake_search),
            patch.object(svc, "is_episode_downloaded", AsyncMock(return_value=False)),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == [(1, None), (1, 1)]

    def test_legacy_all_undated_show_only_searches_first_season(self) -> None:
        show = make_show(air_date=None)
        show.seasons.append(
            Season(
                id=SeasonId(uuid.uuid4()),
                show_id=show.id,
                number=SeasonNumber(2),
                episodes=[
                    Episode(
                        number=EpisodeNumber(1),
                        title="TBA",
                        air_date=None,
                    )
                ],
            )
        )
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list[tuple[int | None, int | None]] = []

        async def fake_search(*_args, **kwargs):
            searched.append((kwargs.get("season_number"), kwargs.get("episode_number")))
            return []

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(svc, "get_all_available_torrents_for_an_episode", fake_search),
            patch.object(svc, "is_episode_downloaded", AsyncMock(return_value=False)),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == [(1, None), (1, 1)]

    def test_later_season_with_only_future_and_unknown_dates_not_searched(
        self,
    ) -> None:
        yesterday = datetime.now().astimezone().date() - timedelta(days=1)
        tomorrow = datetime.now().astimezone().date() + timedelta(days=1)
        show = make_show(air_date=yesterday)
        show.seasons.append(
            Season(
                id=SeasonId(uuid.uuid4()),
                show_id=show.id,
                number=SeasonNumber(2),
                episodes=[
                    Episode(
                        number=EpisodeNumber(1),
                        title="Announced",
                        air_date=tomorrow,
                    ),
                    Episode(
                        number=EpisodeNumber(2),
                        title="TBA",
                        air_date=None,
                    ),
                ],
            )
        )
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list[tuple[int | None, int | None]] = []

        async def fake_search(*_args, **kwargs):
            searched.append((kwargs.get("season_number"), kwargs.get("episode_number")))
            return []

        async def is_downloaded(*, episode, **_kwargs):
            return episode.air_date == yesterday

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
            patch.object(svc, "get_all_available_torrents_for_an_episode", fake_search),
            patch.object(svc, "is_episode_downloaded", is_downloaded),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={}),
            ),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == []

    def test_skipped_show_not_processed(self) -> None:
        show = make_show(skipped=True)
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=show_repo)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_bg():
            yield svc

        searched: list = []

        async def fake_search(*_args, **_kwargs):
            searched.append(True)
            return []

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(svc, "get_all_available_torrents_for_a_season", fake_search),
        ):
            from miramedia.shows.service import _auto_download_for_show_impl

            run_async(_auto_download_for_show_impl(show, max_downloads=5))

        assert searched == []

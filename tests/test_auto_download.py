"""Characterization tests for auto-download selection gates beyond sweep candidates."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import EpisodeFile
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

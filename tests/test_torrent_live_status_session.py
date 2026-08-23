"""Regression: live torrent-status fan-out releases the DB session first."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from miramedia.torrents.manager import DownloadManager
from miramedia.torrents.schemas import Quality, TorrentStatus
from miramedia.torrents.schemas import Torrent as TorrentSchema
from miramedia.torrents.service import TorrentService
from tests.fakes.repositories import FakeTorrentRepository


def _run(coro):
    return asyncio.run(coro)


def test_fetch_live_torrent_statuses_releases_session_before_rpc(
    monkeypatch,
) -> None:
    torrent = TorrentSchema(
        id=uuid.uuid4(),
        status=TorrentStatus.downloading,
        title="Active",
        quality=Quality.hd,
        hash="a" * 40,
        usenet=False,
    )
    repo = FakeTorrentRepository()
    repo.torrents[torrent.id] = torrent
    repo.db = MagicMock()
    dm = MagicMock(spec=DownloadManager)
    dm._torrent_client = MagicMock()
    svc = TorrentService(torrent_repository=repo, download_manager=dm)  # type: ignore[arg-type]

    order: list[str] = []

    async def _release(_db: object) -> None:
        order.append("release")

    async def _get_status(t: TorrentSchema, *, persist: bool = True) -> TorrentSchema:
        order.append("get_torrent_status")
        assert persist is False
        return t

    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io",
        _release,
    )
    monkeypatch.setattr(svc, "get_torrent_status", _get_status)

    result = _run(svc._fetch_live_torrent_statuses([torrent]))

    assert [t.id for t in result] == [torrent.id]
    assert order.index("release") < order.index("get_torrent_status")


def test_fetch_live_torrent_statuses_skips_release_when_empty(monkeypatch) -> None:
    release = AsyncMock()
    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io",
        release,
    )
    repo = FakeTorrentRepository()
    svc = TorrentService(torrent_repository=repo)  # type: ignore[arg-type]

    assert _run(svc._fetch_live_torrent_statuses([])) == []
    release.assert_not_awaited()

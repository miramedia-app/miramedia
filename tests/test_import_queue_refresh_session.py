"""Regression: per-torrent import-queue refresh releases the DB session first."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from miramedia.exceptions import NotFoundError
from miramedia.imports.queue.refresh import sync_torrent_import_queue
from miramedia.torrents.schemas import (
    ImportProgress,
    ImportStatusEntry,
    Quality,
    TorrentId,
    TorrentStatus,
)
from miramedia.torrents.schemas import Torrent as TorrentSchema


def _run(coro):
    return asyncio.run(coro)


def test_sync_torrent_import_queue_releases_session_before_rpc(
    monkeypatch,
) -> None:
    torrent_id = uuid.uuid4()
    torrent = TorrentSchema(
        id=torrent_id,
        status=TorrentStatus.finished,
        title="Done",
        quality=Quality.hd,
        hash="a" * 40,
        usenet=False,
    )

    calls: list[str] = []

    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    torrent_service = MagicMock()
    torrent_service.torrent_repository.get_torrent_by_id = AsyncMock(
        return_value=torrent
    )

    async def _get_status(t: TorrentSchema, *, persist: bool = True) -> TorrentSchema:
        calls.append("rpc")
        assert persist is False
        return t

    torrent_service.get_torrent_status = _get_status

    entry = ImportStatusEntry(
        torrent_id=TorrentId(torrent_id),
        torrent_title="Done",
        torrent_status=TorrentStatus.finished,
        progress=ImportProgress(total=0),
        files=[],
    )
    torrent_service._build_import_status_entry = AsyncMock(return_value=entry)
    torrent_service.is_import_ready = MagicMock(return_value=False)

    service = MagicMock()
    service.torrent_service = torrent_service

    async def _release(_db: object) -> None:
        calls.append("release")

    monkeypatch.setattr(
        "miramedia.imports.queue.refresh.release_session_before_external_io",
        _release,
    )

    _run(sync_torrent_import_queue(db, service, torrent_id))

    assert calls == ["release", "rpc"]


def test_sync_torrent_import_queue_not_found_skips_release(monkeypatch) -> None:
    torrent_id = uuid.uuid4()
    calls: list[str] = []

    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    torrent_service = MagicMock()

    async def _get_by_id(_tid: TorrentId) -> TorrentSchema:
        msg = "gone"
        raise NotFoundError(msg)

    torrent_service.torrent_repository.get_torrent_by_id = _get_by_id

    service = MagicMock()
    service.torrent_service = torrent_service

    async def _release(_db: object) -> None:
        calls.append("release")

    monkeypatch.setattr(
        "miramedia.imports.queue.refresh.release_session_before_external_io",
        _release,
    )

    _run(sync_torrent_import_queue(db, service, torrent_id))

    assert calls == []
    assert db.execute.await_count == 2
    db.commit.assert_awaited_once()

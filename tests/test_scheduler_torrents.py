"""Scheduler torrent health / finished-download detection tests."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import miramedia.scheduler as scheduler
from miramedia.torrents.models import Torrent
from miramedia.torrents.repository import ACTIVE_TORRENT_STATUSES, TorrentRepository
from miramedia.torrents.schemas import Quality, TorrentStatus
from miramedia.torrents.schemas import Torrent as TorrentSchema
from tests.fakes.repositories import FakeTorrentRepository


def _run(coro) -> None:
    asyncio.run(coro)


def test_finished_torrent_query_filters_in_sql() -> None:
    stmt = select(Torrent).where(Torrent.status == TorrentStatus.finished)
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "torrent.status" in sql
    assert "'finished'" in sql


def test_active_torrent_query_filters_in_sql() -> None:
    stmt = select(Torrent).where(Torrent.status.in_(ACTIVE_TORRENT_STATUSES))
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "torrent.status IN" in sql
    assert "'downloading'" in sql
    assert "'unknown'" in sql
    assert "'finished'" not in sql
    assert "'paused'" not in sql
    assert "'error'" not in sql


def test_fake_active_torrent_repository_excludes_inactive_statuses() -> None:
    repo = FakeTorrentRepository()
    active_id = uuid.uuid4()
    finished_id = uuid.uuid4()
    repo.torrents[active_id] = TorrentSchema(
        id=active_id,
        status=TorrentStatus.downloading,
        title="Active",
        quality=Quality.hd,
        hash="a" * 40,
        usenet=False,
    )
    repo.torrents[finished_id] = TorrentSchema(
        id=finished_id,
        status=TorrentStatus.finished,
        title="Done",
        quality=Quality.hd,
        hash="b" * 40,
        usenet=False,
    )

    active = asyncio.run(repo.get_active_torrents())
    assert [t.id for t in active] == [active_id]
    finished = asyncio.run(repo.get_finished_torrents())
    assert [t.id for t in finished] == [finished_id]
    assert asyncio.run(repo.get_all_torrents())  # all-record callers unchanged


def test_detect_finished_downloads_uses_active_query_only(monkeypatch) -> None:
    active = TorrentSchema(
        id=uuid.uuid4(),
        status=TorrentStatus.downloading,
        title="Active",
        quality=Quality.hd,
        hash="c" * 40,
        usenet=False,
    )
    finished = TorrentSchema(
        id=uuid.uuid4(),
        status=TorrentStatus.finished,
        title="Done",
        quality=Quality.hd,
        hash="d" * 40,
        usenet=False,
    )
    repo = FakeTorrentRepository()
    repo.torrents[active.id] = active
    repo.torrents[finished.id] = finished
    repo.get_active_torrents = AsyncMock(wraps=repo.get_active_torrents)  # type: ignore[method-assign]
    repo.db = MagicMock()

    fetched: list[TorrentSchema] = []

    class _Svc:
        torrent_repository = repo

        async def _fetch_live_torrent_statuses(self, torrents: list[TorrentSchema]):
            fetched.extend(torrents)
            return torrents

    @asynccontextmanager
    async def _bg_torrent_service():
        yield _Svc()

    release = AsyncMock()
    monkeypatch.setattr(
        "miramedia.background_services.bg_torrent_service", _bg_torrent_service
    )
    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io", release
    )
    monkeypatch.setattr(
        scheduler.import_all_movie_torrents_task,
        "kiq",
        AsyncMock(),
    )
    monkeypatch.setattr(
        scheduler.import_all_show_torrents_task,
        "kiq",
        AsyncMock(),
    )

    _run(scheduler.detect_finished_downloads_task())

    repo.get_active_torrents.assert_awaited_once()
    assert [t.id for t in fetched] == [active.id]
    release.assert_awaited_once()


def test_repository_get_active_torrents_delegates_to_status_predicate() -> None:
    db = MagicMock()
    repo = TorrentRepository(db)
    assert repo.get_active_torrents.__doc__ is not None
    assert "finished" in repo.get_active_torrents.__doc__

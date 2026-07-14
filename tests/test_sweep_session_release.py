"""Regression: torrent/import sweeps release the DB session before slow I/O."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from miramedia.torrents.schemas import Quality, TorrentStatus
from miramedia.torrents.schemas import Torrent as TorrentSchema
from miramedia.torrents.service import TorrentService
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import FakeTorrentRepository, make_torrent


def _run(coro):
    return asyncio.run(coro)


def test_get_all_torrents_releases_session_before_rpc(monkeypatch) -> None:
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
    svc = TorrentService(torrent_repository=repo)  # type: ignore[arg-type]

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

    result = _run(svc.get_all_torrents())

    assert [t.id for t in result] == [torrent.id]
    assert order.index("release") < order.index("get_torrent_status")


def test_import_all_torrents_releases_session_before_import_loop() -> None:
    torrent = make_torrent()
    svc, _, torrent_repo = build_show_service(torrent_repo=FakeTorrentRepository())
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.db = MagicMock()

    fresh_svc, _, _ = build_show_service()

    order: list[str] = []
    call_count = 0

    @asynccontextmanager
    async def fake_bg():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield svc
        else:
            yield fresh_svc

    async def _release(_db: object) -> None:
        order.append("release")

    async def _import(*_args, **_kwargs) -> None:
        order.append("import")

    with (
        patch("miramedia.database.bg_show_service", fake_bg),
        patch(
            "miramedia.database.release_session_before_external_io",
            side_effect=_release,
        ),
        patch.object(
            svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
        ),
        patch.object(
            svc.torrent_service,
            "get_all_torrents",
            AsyncMock(return_value=[torrent]),
        ),
        patch.object(
            svc.torrent_service,
            "bulk_check_torrents_imported",
            AsyncMock(return_value={torrent.id: False}),
        ),
        patch.object(
            svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
        ),
        patch.object(svc, "_import_media_from_torrent", _import),
        patch.object(
            fresh_svc.torrent_service.torrent_repository,
            "get_torrent_by_id",
            AsyncMock(return_value=torrent),
        ),
        patch.object(svc, "_get_media_of_torrent", AsyncMock(return_value=object())),
    ):
        run_async(svc.import_all_torrents())

    assert order.index("release") < order.index("import")

"""Hardened imports ignore-torrent: session release, best-effort delete, queue prune."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.exceptions import NotFoundError
from miramedia.imports.schemas import IgnoreRequest
from miramedia.imports.service import ImportsService

_RELEASE_PATCH = "miramedia.database.release_session_before_external_io"


@pytest.fixture(autouse=True)
def _mock_release_session() -> None:
    with patch(_RELEASE_PATCH, new_callable=AsyncMock):
        yield


def _service() -> ImportsService:
    repository = MagicMock()
    repository.db.execute = AsyncMock()
    repository.db.commit = AsyncMock()
    torrent_service = MagicMock()
    torrent_service.torrent_repository.get_torrent_by_id = AsyncMock()
    torrent_service.cancel_download = AsyncMock()
    torrent_service.delete_torrent = AsyncMock()
    return ImportsService(
        repository=repository,
        torrent_service=torrent_service,
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )


@pytest.mark.anyio
async def test_ignore_torrent_delete_failure_still_prunes_queue() -> None:
    service = _service()
    torrent_id = uuid.uuid4()
    torrent = MagicMock(id=torrent_id)
    service.torrent_service.torrent_repository.get_torrent_by_id.return_value = torrent
    service.torrent_service.delete_torrent.side_effect = NotFoundError("gone")

    result = await service.ignore(
        IgnoreRequest(kind="torrent", id=str(torrent_id), delete_files=True)
    )

    assert result.ok
    service.repository.db.execute.assert_awaited_once()
    service.repository.db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_ignore_torrent_releases_session_before_cancel() -> None:
    call_order: list[str] = []

    async def _release(_db: object) -> None:
        call_order.append("release")

    async def _cancel(**_kwargs: object) -> None:
        call_order.append("cancel")

    service = _service()
    torrent_id = uuid.uuid4()
    torrent = MagicMock(id=torrent_id)
    service.torrent_service.torrent_repository.get_torrent_by_id.return_value = torrent
    service.torrent_service.cancel_download.side_effect = _cancel

    with patch(_RELEASE_PATCH, side_effect=_release):
        result = await service.ignore(
            IgnoreRequest(kind="torrent", id=str(torrent_id), delete_files=False)
        )

    assert result.ok
    assert call_order == ["release", "cancel"]


@pytest.mark.anyio
async def test_ignore_torrent_cancel_failure_still_deletes_and_prunes() -> None:
    service = _service()
    torrent_id = uuid.uuid4()
    torrent = MagicMock(id=torrent_id)
    service.torrent_service.torrent_repository.get_torrent_by_id.return_value = torrent
    service.torrent_service.cancel_download.side_effect = RuntimeError("client down")

    result = await service.ignore(
        IgnoreRequest(kind="torrent", id=str(torrent_id), delete_files=True)
    )

    assert result.ok
    service.torrent_service.delete_torrent.assert_awaited_once_with(
        torrent_id=torrent_id
    )
    service.repository.db.execute.assert_awaited_once()
    service.repository.db.commit.assert_awaited_once()

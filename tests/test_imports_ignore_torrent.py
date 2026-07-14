"""Deleting a torrent from the imports page must actually make it go away.

Regression: ``ImportsService.ignore`` used to call the raw repository delete,
which left the torrent running in the download client and — because the
imports page is queue-backed — left the stale ``ImportQueueItem`` rows in
place, so the entry never disappeared from the UI.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.exceptions import NotFoundError
from miramedia.imports.schemas import IgnoreRequest
from miramedia.imports.service import ImportsService


@pytest.fixture(autouse=True)
def _mock_release_session() -> Iterator[None]:
    # ignore() releases the DB session before the download-client RPC; the
    # real helper commits/closes, which a MagicMock session can't satisfy.
    with patch(
        "miramedia.database.release_session_before_external_io",
        new_callable=AsyncMock,
    ):
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
async def test_ignore_torrent_stops_client_and_prunes_queue() -> None:
    service = _service()
    torrent_id = uuid.uuid4()
    torrent = MagicMock(id=torrent_id)
    service.torrent_service.torrent_repository.get_torrent_by_id.return_value = torrent

    result = await service.ignore(
        IgnoreRequest(kind="torrent", id=str(torrent_id), delete_files=True)
    )

    assert result.ok
    service.torrent_service.cancel_download.assert_awaited_once_with(
        torrent=torrent, delete_files=True
    )
    service.torrent_service.delete_torrent.assert_awaited_once_with(
        torrent_id=torrent_id
    )
    # Queue rows pruned in-request so the UI refetch doesn't see the stale row.
    service.repository.db.execute.assert_awaited_once()
    service.repository.db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_ignore_torrent_missing_row_still_prunes_queue() -> None:
    service = _service()
    torrent_id = uuid.uuid4()
    service.torrent_service.torrent_repository.get_torrent_by_id.side_effect = (
        NotFoundError("gone")
    )

    result = await service.ignore(
        IgnoreRequest(kind="torrent", id=str(torrent_id), delete_files=True)
    )

    assert result.ok
    service.torrent_service.cancel_download.assert_not_awaited()
    service.torrent_service.delete_torrent.assert_not_awaited()
    service.repository.db.execute.assert_awaited_once()
    service.repository.db.commit.assert_awaited_once()

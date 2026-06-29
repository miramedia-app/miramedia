"""Regression: ``TorrentRepository.delete_torrent`` must commit.

Without the commit the row-delete is only *staged* on the session. A caller
that re-raises after a link failure (``download_and_link`` cleanup) then
triggers a rollback that undoes the delete, stranding the torrent as an
"Unlinked" ghost on the torrents page — while its file rows (removed via their
own committing helpers) are really gone.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from miramedia.torrents.repository import TorrentRepository


def test_delete_torrent_commits_even_when_row_missing():
    db = MagicMock()
    db.execute = AsyncMock()
    db.get = AsyncMock(return_value=None)  # torrent already gone
    db.delete = AsyncMock()
    db.commit = AsyncMock()

    asyncio.run(TorrentRepository(db).delete_torrent(torrent_id=uuid.uuid4()))

    db.commit.assert_awaited_once()


def test_delete_torrent_commits_after_deleting_row():
    torrent = MagicMock()
    torrent.hash = "abc123"

    db = MagicMock()
    db.execute = AsyncMock()
    db.get = AsyncMock(return_value=torrent)
    db.delete = AsyncMock()
    db.commit = AsyncMock()

    asyncio.run(TorrentRepository(db).delete_torrent(torrent_id=uuid.uuid4()))

    db.delete.assert_awaited_once_with(torrent)
    db.commit.assert_awaited_once()

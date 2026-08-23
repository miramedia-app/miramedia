"""Native backend bulk torrent-status lookups (Plan 398)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import libtorrent
import pytest

from miramedia.torrents.backends import native as native_backend
from miramedia.torrents.backends.native import NativeDownloadClient
from miramedia.torrents.schemas import Quality, Torrent, TorrentStatus


def _hash(n: int) -> str:
    return f"{n:040x}"


def _make_torrent(info_hash: str, title: str = "title") -> Torrent:
    return Torrent(
        id=uuid.uuid4(),
        status=TorrentStatus.downloading,
        title=title,
        quality=Quality.hd,
        hash=info_hash,
        usenet=False,
    )


def _make_handle(
    info_hash: str,
    *,
    state: object,
    progress: float = 0.5,
    num_peers: int = 3,
    num_seeds: int = 2,
    download_rate: int = 1000,
    errc: int = 0,
) -> MagicMock:
    handle = MagicMock()
    handle.info_hash.return_value = info_hash
    status = MagicMock()
    status.state = state
    status.progress = progress
    status.num_peers = num_peers
    status.num_seeds = num_seeds
    status.download_rate = download_rate
    status.errc.value.return_value = errc
    handle.status.return_value = status
    return handle


@pytest.fixture
def native_client(tmp_path: Path) -> NativeDownloadClient:
    native_backend.NativeDownloadClient._instance = None
    resume_dir = tmp_path / ".resume_data"
    resume_dir.mkdir()

    client = object.__new__(NativeDownloadClient)
    client._initialized = True
    client._resume_data_dir = resume_dir
    client._session = MagicMock()
    client._moved_hashes = set()

    yield client

    native_backend.NativeDownloadClient._instance = None


def test_bulk_status_enumerates_handles_once(
    native_client: NativeDownloadClient,
) -> None:
    hashes = [_hash(i) for i in range(8)]
    handles = [
        _make_handle(
            h,
            state=libtorrent.torrent_status.states.downloading,
            progress=0.42,
        )
        for h in hashes
    ]
    native_client._session.get_torrents.return_value = handles
    torrents = [_make_torrent(h) for h in hashes]

    result = native_client.get_torrent_statuses_bulk(torrents)

    native_client._session.get_torrents.assert_called_once()
    assert set(result) == set(hashes)
    for h in hashes:
        status, progress, peers, seeds, speed = result[h]
        assert status == TorrentStatus.downloading
        assert progress == 42.0
        assert peers == 3
        assert seeds == 2
        assert speed == 1000


def test_bulk_status_unknown_hash_returns_not_found_shape(
    native_client: NativeDownloadClient,
) -> None:
    known = _hash(1)
    unknown = _hash(2)
    native_client._session.get_torrents.return_value = [
        _make_handle(known, state=libtorrent.torrent_status.states.seeding)
    ]

    result = native_client.get_torrent_statuses_bulk(
        [_make_torrent(known), _make_torrent(unknown)]
    )

    native_client._session.get_torrents.assert_called_once()
    assert result[known] == (TorrentStatus.finished, 100.0, 3, 2, 0)
    assert result[unknown] == (TorrentStatus.unknown, 0.0, 0, 0, 0)

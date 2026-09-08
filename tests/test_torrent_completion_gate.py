"""DB-free tests for TorrentService.reject_decoy_downloads.

The completion gate is the backstop that the post-add verifier is not: at
finish time the download client always knows the full file list, so a decoy
payload cannot slip through by resolving its metadata slowly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from miramedia.torrents.inspection import TorrentFile
from miramedia.torrents.service import TorrentService
from tests.fakes.repositories import FakeTorrentRepository, make_torrent

_MIN_MEANINGFUL_BYTES = 50 * 1024 * 1024


class _StubDownloadManager:
    """Returns a scripted value (or raises it) from ``get_torrent_files``."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls = 0

    def get_torrent_files(self, torrent: object) -> list[TorrentFile] | None:
        del torrent
        self.calls += 1
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


def _make_service(download_manager: object) -> TorrentService:
    return TorrentService(
        torrent_repository=FakeTorrentRepository(),  # type: ignore[arg-type]
        download_manager=download_manager,  # type: ignore[arg-type]
    )


def _run_gate(svc: TorrentService, torrents: list[Any]) -> tuple[list[Any], AsyncMock]:
    block_mock = AsyncMock()
    with patch.object(TorrentService, "_block_and_remove", block_mock):
        kept = asyncio.run(svc.reject_decoy_downloads(torrents))
    return kept, block_mock


def _real_video_files() -> list[TorrentFile]:
    return [TorrentFile(path=Path("Show.S01E01.mkv"), size=_MIN_MEANINGFUL_BYTES + 1)]


def _exe_only_files() -> list[TorrentFile]:
    """The real-world payload: one big renamed binary, no video at all."""
    return [
        TorrentFile(
            path=Path("Lioness S03E06 1080p WEBRip H264-KRAFT.exe"),
            size=1_074_581_504,
        )
    ]


def _decoy_files() -> list[TorrentFile]:
    """Tiny dummy video next to the payload, to defeat a naive 'any video?'."""
    return [
        TorrentFile(path=Path("setup.exe"), size=5_000_000),
        TorrentFile(path=Path("video.mkv"), size=1_000_000),
    ]


def test_real_release_is_kept() -> None:
    svc = _make_service(_StubDownloadManager(_real_video_files()))
    torrent = make_torrent()

    kept, block_mock = _run_gate(svc, [torrent])

    assert kept == [torrent]
    block_mock.assert_not_called()


def test_exe_only_payload_is_rejected_and_deny_listed() -> None:
    svc = _make_service(_StubDownloadManager(_exe_only_files()))

    kept, block_mock = _run_gate(svc, [make_torrent()])

    assert kept == []
    block_mock.assert_awaited_once()
    _torrent, kwargs = block_mock.await_args
    assert kwargs == {"reason": "no_meaningful_video"}


def test_tiny_decoy_video_is_rejected() -> None:
    svc = _make_service(_StubDownloadManager(_decoy_files()))

    kept, block_mock = _run_gate(svc, [make_torrent()])

    assert kept == []
    block_mock.assert_awaited_once()


def test_missing_file_list_fails_open() -> None:
    """No data to judge on — keep the torrent; the import scan still screens it."""
    svc = _make_service(_StubDownloadManager(None))
    torrent = make_torrent()

    kept, block_mock = _run_gate(svc, [torrent])

    assert kept == [torrent]
    block_mock.assert_not_called()


def test_client_error_fails_open() -> None:
    svc = _make_service(_StubDownloadManager(RuntimeError("client unavailable")))
    torrent = make_torrent()

    kept, block_mock = _run_gate(svc, [torrent])

    assert kept == [torrent]
    block_mock.assert_not_called()


def test_gate_is_per_torrent() -> None:
    """One bad torrent must not take its healthy batch-mates down with it."""

    class _PerTitleManager:
        def get_torrent_files(self, torrent: Any) -> list[TorrentFile]:
            return _exe_only_files() if "exe" in torrent.title else _real_video_files()

    svc = _make_service(_PerTitleManager())
    good = make_torrent(title="Show S01E01 1080p WEB h264-GRP")
    bad = make_torrent(title="Show S01E02 1080p WEB h264-GRP exe")

    kept, block_mock = _run_gate(svc, [good, bad])

    assert [t.title for t in kept] == [good.title]
    assert block_mock.await_count == 1

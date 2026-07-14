"""Characterization tests for TorrentService._verify_torrent_has_video_files.

Pins the post-add decoy verifier's destructive vs fail-open branches without
touching production code or requiring Postgres.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from miramedia.torrents.service import TorrentService
from miramedia.torrents.utils import TorrentFile
from tests.fakes.repositories import FakeTorrentRepository, make_torrent

_MIN_MEANINGFUL_BYTES = 50 * 1024 * 1024


class _ScriptedDownloadManager:
    """Sync fake whose ``get_torrent_files`` pops scripted responses per call."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)

    def get_torrent_files(self, torrent: object) -> list[TorrentFile] | None:
        del torrent
        if not self._responses:
            return None
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _AlwaysNoneDownloadManager:
    def get_torrent_files(self, torrent: object) -> None:
        del torrent


def _make_service(download_manager: object) -> TorrentService:
    torrent_repo = FakeTorrentRepository()
    return TorrentService(
        torrent_repository=torrent_repo,  # type: ignore[arg-type]
        download_manager=download_manager,  # type: ignore[arg-type]
    )


def _real_video_files() -> list[TorrentFile]:
    return [
        TorrentFile(
            path=Path("Movie.2020.1080p.mkv"),
            size=_MIN_MEANINGFUL_BYTES + 1,
        )
    ]


def _decoy_only_files() -> list[TorrentFile]:
    """Tiny non-sample video plus an exe — below the meaningful-size threshold."""
    return [
        TorrentFile(path=Path("setup.exe"), size=5_000_000),
        TorrentFile(path=Path("video.mkv"), size=1_000_000),
    ]


def test_harness_imports() -> None:
    svc = _make_service(_AlwaysNoneDownloadManager())
    assert svc.download_manager is not None


def test_real_video_present_skips_removal() -> None:
    svc = _make_service(_ScriptedDownloadManager([_real_video_files()]))
    block_mock = AsyncMock()
    with patch.object(TorrentService, "_block_and_remove", block_mock):
        asyncio.run(
            svc._verify_torrent_has_video_files(
                make_torrent(),
                timeout_seconds=0.2,
                poll_interval_seconds=0.01,
            )
        )
    block_mock.assert_not_called()


def test_decoy_only_payload_triggers_removal() -> None:
    svc = _make_service(_ScriptedDownloadManager([_decoy_only_files()]))
    block_mock = AsyncMock()
    with patch.object(TorrentService, "_block_and_remove", block_mock):
        asyncio.run(
            svc._verify_torrent_has_video_files(
                make_torrent(),
                timeout_seconds=0.2,
                poll_interval_seconds=0.01,
            )
        )
    block_mock.assert_awaited_once()
    _torrent, kwargs = block_mock.await_args
    assert kwargs == {"reason": "no_meaningful_video"}


def test_metadata_timeout_fails_open() -> None:
    svc = _make_service(_AlwaysNoneDownloadManager())
    block_mock = AsyncMock()
    with patch.object(TorrentService, "_block_and_remove", block_mock):
        asyncio.run(
            svc._verify_torrent_has_video_files(
                make_torrent(),
                timeout_seconds=0.2,
                poll_interval_seconds=0.01,
            )
        )
    block_mock.assert_not_called()


def test_client_error_then_recovery_keeps_torrent() -> None:
    svc = _make_service(
        _ScriptedDownloadManager(
            [
                RuntimeError("client unavailable"),
                _real_video_files(),
            ]
        )
    )
    block_mock = AsyncMock()
    with patch.object(TorrentService, "_block_and_remove", block_mock):
        asyncio.run(
            svc._verify_torrent_has_video_files(
                make_torrent(),
                timeout_seconds=0.2,
                poll_interval_seconds=0.01,
            )
        )
    block_mock.assert_not_called()

"""Leftover torrent-directory cleanup.

Production symptom these guard: a deleted movie left an empty completed dir
and an empty incomplete dir behind, a later re-download of the same release
resolved onto one of those empty leftovers, and every import retry reported
"source files missing".
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import miramedia.torrents.backends.native as native_backend
from miramedia.scheduler_tasks import maintenance
from miramedia.torrents import paths
from miramedia.torrents.backends.native import NativeDownloadClient
from miramedia.torrents.models import Quality
from miramedia.torrents.schemas import Torrent, TorrentStatus


@pytest.fixture
def roots(tmp_path, monkeypatch):
    completed = tmp_path / "completed"
    incomplete = tmp_path / "incomplete"
    completed.mkdir()
    incomplete.mkdir()

    def config():
        return SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path=str(incomplete),
                torrent_directory=str(completed),
            ),
            torrents=SimpleNamespace(
                native=SimpleNamespace(
                    listen_port_start=6881,
                    max_download_rate=0,
                    max_upload_rate=0,
                ),
            ),
        )

    import miramedia.torrents.backends.native as native_backend

    monkeypatch.setattr(native_backend, "MiraMediaConfig", config)
    monkeypatch.setattr(paths, "MiraMediaConfig", config)
    monkeypatch.setattr(maintenance, "MiraMediaConfig", config)
    return SimpleNamespace(completed=completed, incomplete=incomplete)


def _torrent(title: str, torrent_hash: str = "a" * 40) -> Torrent:
    return Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash=torrent_hash,
        usenet=False,
    )


def _age(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def _status(save_path: Path, *, moving: bool = False) -> SimpleNamespace:
    return SimpleNamespace(save_path=str(save_path), moving_storage=moving)


class _FakeHandle:
    """Records move_storage calls; no libtorrent session involved."""

    def __init__(self) -> None:
        self.moves: list[str] = []

    def move_storage(self, path: str, _flags: object = None) -> None:
        self.moves.append(path)


def test_finished_is_withheld_until_move_lands(roots) -> None:
    """A finished torrent stays "downloading" while its payload is in incomplete."""
    client = NativeDownloadClient()
    torrent = _torrent("Some.Release.2020.1080p", "d" * 40)
    handle = _FakeHandle()
    source = roots.incomplete / torrent.title
    source.mkdir()

    # First poll issues the move; payload has not arrived in completed yet.
    assert client._ensure_moved_to_completed(handle, torrent, _status(source)) is False
    assert handle.moves == [str(roots.completed / torrent.title)]

    # Move in flight: no second move_storage, still not importable.
    assert (
        client._ensure_moved_to_completed(handle, torrent, _status(source, moving=True))
        is False
    )
    assert len(handle.moves) == 1

    # libtorrent reports the new save path: importable, source dir reaped.
    landed = _status(roots.completed / torrent.title)
    assert client._ensure_moved_to_completed(handle, torrent, landed) is True
    assert not source.exists()


def test_stalled_move_is_retried(roots) -> None:
    client = NativeDownloadClient()
    torrent = _torrent("Stalled.Release.2020.1080p", "e" * 40)
    handle = _FakeHandle()
    source = roots.incomplete / torrent.title
    source.mkdir()

    assert client._ensure_moved_to_completed(handle, torrent, _status(source)) is False
    # Within the retry window nothing is re-issued.
    assert client._ensure_moved_to_completed(handle, torrent, _status(source)) is False
    assert len(handle.moves) == 1

    client._move_attempts[torrent.hash] -= native_backend._MOVE_RETRY_SECONDS + 1
    assert client._ensure_moved_to_completed(handle, torrent, _status(source)) is False
    assert len(handle.moves) == 2


def test_move_is_noop_without_incomplete_root(roots, monkeypatch) -> None:
    """Single-root setups have nothing to move, so imports are never gated."""
    monkeypatch.setattr(
        native_backend,
        "MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=roots.completed,
                incomplete_torrent_path="",
                torrent_directory=str(roots.completed),
            )
        ),
    )
    client = NativeDownloadClient()
    torrent = _torrent("Direct.Release.2020.1080p", "f" * 40)
    handle = _FakeHandle()

    assert (
        client._ensure_moved_to_completed(
            handle, torrent, _status(roots.completed / torrent.title)
        )
        is True
    )
    assert handle.moves == []


def test_moved_source_dir_is_reaped_once_empty(roots) -> None:
    """move_storage is async — the emptied source dir is dropped on a later poll."""
    client = NativeDownloadClient()
    torrent = _torrent("Some.Release.2020.1080p")
    source = roots.incomplete / torrent.title
    (source / "Subs").mkdir(parents=True)
    client._pending_move_cleanup[torrent.hash] = source

    client._prune_moved_source_dir(torrent.hash)

    assert not source.exists()
    assert torrent.hash not in client._pending_move_cleanup


def test_moved_source_dir_kept_while_move_in_flight(roots) -> None:
    client = NativeDownloadClient()
    torrent = _torrent("Some.Release.2020.1080p", "b" * 40)
    source = roots.incomplete / torrent.title
    source.mkdir()
    (source / "movie.mkv").write_bytes(b"x")
    client._pending_move_cleanup[torrent.hash] = source

    client._prune_moved_source_dir(torrent.hash)

    assert (source / "movie.mkv").exists()
    # Still pending, so a later poll retries once the move lands.
    assert client._pending_move_cleanup[torrent.hash] == source


def test_remove_torrent_without_handle_reaps_empty_save_dirs(roots) -> None:
    """An orphaned row still cleans its empty dirs, so a re-download is clean."""
    client = NativeDownloadClient()
    torrent = _torrent("Orphaned.Release.2020.1080p", "c" * 40)
    stale_completed = roots.completed / torrent.title
    stale_incomplete = roots.incomplete / torrent.title
    stale_completed.mkdir()
    stale_incomplete.mkdir()

    client.remove_torrent(torrent, delete_data=True)

    assert not stale_completed.exists()
    assert not stale_incomplete.exists()


def test_prune_sweep_removes_old_empty_trees_only(roots) -> None:
    empty = roots.incomplete / "Old.Empty.Release"
    (empty / "Subs").mkdir(parents=True)
    _age(empty / "Subs", 3)
    _age(empty, 3)

    populated = roots.incomplete / "Old.Populated.Release"
    populated.mkdir()
    (populated / "movie.mkv").write_bytes(b"x")
    _age(populated, 3)

    fresh = roots.incomplete / "Fresh.Empty.Release"
    fresh.mkdir()

    removed = maintenance._prune_empty_torrent_dirs_sync()

    assert removed == 1
    assert not empty.exists()
    assert (populated / "movie.mkv").exists()
    assert fresh.exists()
    assert roots.incomplete.exists()
    assert roots.completed.exists()


def test_prune_sweep_leaves_resume_data_dir(roots) -> None:
    resume = roots.completed / ".resume_data"
    resume.mkdir()
    _age(resume, 30)

    assert maintenance._prune_empty_torrent_dirs_sync() == 0
    assert resume.exists()


def test_prune_sweep_never_follows_symlinked_dirs(roots, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = roots.incomplete / "Linked.Release"
    link.symlink_to(outside)
    _age(outside, 30)

    assert maintenance._prune_empty_torrent_dirs_sync() == 0
    assert outside.exists()
    assert link.is_symlink()


def test_import_cleanup_reaps_dirs_and_sidecar(roots) -> None:
    """After cleanup_after_import removes the payload, nothing is left behind."""
    client = NativeDownloadClient()
    torrent = _torrent("Imported.Release.2020.1080p", "9" * 40)
    completed_dir = roots.completed / torrent.title
    (completed_dir / "Subs").mkdir(parents=True)
    (roots.incomplete / torrent.title).mkdir()
    sidecar = roots.completed / f"{torrent.title}.torrent"
    sidecar.write_bytes(b"d")

    client._try_rmdir_empty_save_dirs(torrent, wait_seconds=0)
    client._remove_sidecar_file(torrent)

    assert not completed_dir.exists()
    assert not (roots.incomplete / torrent.title).exists()
    assert not sidecar.exists()


def test_import_cleanup_keeps_dirs_that_still_hold_files(roots) -> None:
    client = NativeDownloadClient()
    torrent = _torrent("Kept.Release.2020.1080p", "8" * 40)
    completed_dir = roots.completed / torrent.title
    completed_dir.mkdir()
    (completed_dir / "movie.mkv").write_bytes(b"x")

    client._try_rmdir_empty_save_dirs(torrent, wait_seconds=0)

    assert (completed_dir / "movie.mkv").exists()

"""Batch native resume reconciliation at startup (Plan 372)."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.file_status import ImportOutcome
from miramedia.torrents.backends import native as native_backend
from miramedia.torrents.backends.native import (
    NativeDownloadClient,
    _resume_hashes_to_drop,
)


def _hash(n: int) -> str:
    return f"{n:040x}"


@pytest.fixture
def resume_client(tmp_path: Path) -> NativeDownloadClient:
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


def _write_resume(resume_dir: Path, info_hash: str) -> Path:
    path = resume_dir / f"{info_hash}.fastresume"
    path.write_bytes(b"resume")
    return path


class _FakeExecuteResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


def _make_db_session(
    *,
    torrent_rows: list[tuple[uuid.UUID, str]],
    episode_rows: list[tuple[uuid.UUID, ImportOutcome]],
    movie_rows: list[tuple[uuid.UUID, ImportOutcome]],
) -> tuple[MagicMock, list]:
    execute_log: list[object] = []

    async def execute(stmt: object) -> _FakeExecuteResult:
        execute_log.append(stmt)
        if len(execute_log) == 1:
            return _FakeExecuteResult(torrent_rows)
        if len(execute_log) == 2:
            return _FakeExecuteResult(episode_rows)
        return _FakeExecuteResult(movie_rows)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    return db, execute_log


@asynccontextmanager
async def _session_cm(db: MagicMock):
    yield db


def _run_reconcile(
    client: NativeDownloadClient,
    *,
    torrent_rows: list[tuple[uuid.UUID, str]] | None = None,
    episode_rows: list[tuple[uuid.UUID, ImportOutcome]] | None = None,
    movie_rows: list[tuple[uuid.UUID, ImportOutcome]] | None = None,
) -> tuple[int, MagicMock, list]:
    db, execute_log = _make_db_session(
        torrent_rows=torrent_rows or [],
        episode_rows=episode_rows or [],
        movie_rows=movie_rows or [],
    )
    release = AsyncMock()

    with (
        patch(
            "miramedia.database.SessionLocal",
            return_value=_session_cm(db),
        ),
        patch(
            "miramedia.database.release_session_before_external_io",
            release,
        ),
    ):
        reclaimed = asyncio.run(client.reconcile_resume_data())

    release.assert_awaited_once_with(db)
    return reclaimed, db, execute_log


@pytest.mark.parametrize(
    ("resume_hashes", "torrent_id_by_hash", "states_by_tid", "expected"),
    [
        ([], {}, {}, set()),
        ([_hash(1)], {}, {}, {_hash(1)}),
        ([_hash(2)], {_hash(2): uuid.uuid4()}, {}, set()),
        (
            [_hash(3)],
            {_hash(3): (tid := uuid.uuid4())},
            {tid: [ImportOutcome.imported]},
            {_hash(3)},
        ),
        (
            [_hash(4)],
            {_hash(4): (tid := uuid.uuid4())},
            {tid: [ImportOutcome.imported, ImportOutcome.imported]},
            {_hash(4)},
        ),
        (
            [_hash(5)],
            {_hash(5): (tid := uuid.uuid4())},
            {tid: [ImportOutcome.imported, ImportOutcome.pending]},
            set(),
        ),
        (
            [_hash(6)],
            {_hash(6): (tid := uuid.uuid4())},
            {tid: [ImportOutcome.pending]},
            set(),
        ),
    ],
)
def test_resume_hashes_to_drop_decisions(
    resume_hashes: list[str],
    torrent_id_by_hash: dict[str, uuid.UUID],
    states_by_tid: dict[uuid.UUID, list[ImportOutcome]],
    expected: set[str],
) -> None:
    assert (
        _resume_hashes_to_drop(resume_hashes, torrent_id_by_hash, states_by_tid)
        == expected
    )


def test_reconcile_no_resume_files(resume_client: NativeDownloadClient) -> None:
    reclaimed = asyncio.run(resume_client.reconcile_resume_data())
    assert reclaimed == 0
    resume_client._session.get_torrents.assert_not_called()


def test_reconcile_missing_db_torrent_drops_resume(
    resume_client: NativeDownloadClient,
) -> None:
    orphan = _hash(10)
    _write_resume(resume_client._resume_data_dir, orphan)

    reclaimed, _, execute_log = _run_reconcile(resume_client)

    assert reclaimed == 1
    assert not (resume_client._resume_data_dir / f"{orphan}.fastresume").exists()
    assert len(execute_log) == 1
    resume_client._session.get_torrents.assert_called_once()


def test_reconcile_zero_linked_files_keeps_resume(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(11)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)

    reclaimed, _, _ = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
    )

    assert reclaimed == 0
    assert (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()
    resume_client._session.remove_torrent.assert_not_called()


def test_reconcile_all_imported_drops_resume(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(12)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)
    handle = MagicMock()
    resume_client._session.get_torrents.return_value = [handle]
    handle.info_hash.return_value = info_hash

    reclaimed, _, execute_log = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
        episode_rows=[(torrent_id, ImportOutcome.imported)],
        movie_rows=[(torrent_id, ImportOutcome.imported)],
    )

    assert reclaimed == 1
    assert len(execute_log) == 3
    resume_client._session.remove_torrent.assert_called_once_with(handle)
    assert not (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()


def test_reconcile_mixed_pending_imported_keeps_resume(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(13)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)

    reclaimed, _, _ = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
        episode_rows=[(torrent_id, ImportOutcome.imported)],
        movie_rows=[(torrent_id, ImportOutcome.pending)],
    )

    assert reclaimed == 0
    assert (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()
    resume_client._session.remove_torrent.assert_not_called()


def test_reconcile_episode_only_all_imported_drops(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(14)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)

    reclaimed, _, _ = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
        episode_rows=[(torrent_id, ImportOutcome.imported)],
    )

    assert reclaimed == 1
    assert not (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()


def test_reconcile_movie_only_all_imported_drops(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(15)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)

    reclaimed, _, _ = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
        movie_rows=[(torrent_id, ImportOutcome.imported)],
    )

    assert reclaimed == 1
    assert not (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()


def test_reconcile_missing_handle_still_deletes_resume(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(16)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)
    resume_client._session.get_torrents.return_value = []

    reclaimed, _, _ = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
        episode_rows=[(torrent_id, ImportOutcome.imported)],
    )

    assert reclaimed == 1
    resume_client._session.remove_torrent.assert_not_called()
    assert not (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()


def test_reconcile_removal_failure_still_deletes_resume(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(17)
    torrent_id = uuid.uuid4()
    _write_resume(resume_client._resume_data_dir, info_hash)
    handle = MagicMock()
    resume_client._session.get_torrents.return_value = [handle]
    handle.info_hash.return_value = info_hash
    resume_client._session.remove_torrent.side_effect = RuntimeError("boom")

    reclaimed, _, _ = _run_reconcile(
        resume_client,
        torrent_rows=[(torrent_id, info_hash)],
        episode_rows=[(torrent_id, ImportOutcome.imported)],
    )

    assert reclaimed == 1
    assert not (resume_client._resume_data_dir / f"{info_hash}.fastresume").exists()


def test_reconcile_unlink_failure_does_not_count_reclaimed(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(18)
    torrent_id = uuid.uuid4()
    resume_path = _write_resume(resume_client._resume_data_dir, info_hash)
    original_unlink = Path.unlink

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == resume_path:
            msg = "permission denied"
            raise OSError(msg)
        original_unlink(self, missing_ok=missing_ok)

    with patch.object(Path, "unlink", fail_unlink):
        reclaimed, _, _ = _run_reconcile(
            resume_client,
            torrent_rows=[(torrent_id, info_hash)],
            episode_rows=[(torrent_id, ImportOutcome.imported)],
        )

    assert reclaimed == 0
    assert resume_path.exists()


@pytest.mark.parametrize("count", [5, 50])
def test_reconcile_query_count_constant_for_many_resume_files(
    resume_client: NativeDownloadClient,
    count: int,
) -> None:
    torrent_id = uuid.uuid4()
    torrent_rows: list[tuple[uuid.UUID, str]] = []
    episode_rows: list[tuple[uuid.UUID, ImportOutcome]] = []
    for i in range(count):
        info_hash = _hash(100 + i)
        _write_resume(resume_client._resume_data_dir, info_hash)
        torrent_rows.append((uuid.uuid4(), info_hash))
        episode_rows.append((torrent_id, ImportOutcome.imported))

    _, _, execute_log = _run_reconcile(
        resume_client,
        torrent_rows=torrent_rows,
        episode_rows=episode_rows,
    )

    assert len(execute_log) == 3


def test_reconcile_enumerates_handles_once(
    resume_client: NativeDownloadClient,
) -> None:
    for i in range(8):
        _write_resume(resume_client._resume_data_dir, _hash(200 + i))

    _run_reconcile(resume_client)

    resume_client._session.get_torrents.assert_called_once()


def test_reconcile_context_load_failure_skips_sweep(
    resume_client: NativeDownloadClient,
) -> None:
    info_hash = _hash(20)
    resume_path = _write_resume(resume_client._resume_data_dir, info_hash)
    db = MagicMock()

    with (
        patch(
            "miramedia.database.SessionLocal",
            return_value=_session_cm(db),
        ),
        patch.object(
            resume_client,
            "_load_resume_reconcile_context",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch.object(resume_client, "_snapshot_handle_map") as snapshot,
    ):
        reclaimed = asyncio.run(resume_client.reconcile_resume_data())

    assert reclaimed == 0
    snapshot.assert_not_called()
    assert resume_path.exists()
    resume_client._session.get_torrents.assert_not_called()

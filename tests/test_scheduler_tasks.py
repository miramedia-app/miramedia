"""Characterization tests for critical scheduler task bodies (plan 267)."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.sql.dml import Update

import miramedia.scheduler as scheduler
from miramedia.movies.models import MovieFile
from miramedia.requests.schemas import MediaType, RequestStatus
from tests.fakes.config import fake_scheduler_config
from tests.fakes.db import RecordingSession
from tests.fakes.repositories import make_movie, make_show
from tests.fakes.scheduler import (
    FakeFileRow,
    FakeMovieService,
    FakeShowService,
    TrackingRequestService,
    bg_movie_service_factory,
    bg_request_service_factory,
    bg_show_service_factory,
    make_request,
    native_provider,
    patch_audit_repository_lookups,
    patch_batch_resolve_paths,
)


def _run(coro) -> None:
    asyncio.run(coro)


def _all_updates(sessions: list):
    return [update for session in sessions for update in session.updates]


def _update_values(stmt: Update) -> dict[str, Any]:
    return {key.key: value.value for key, value in stmt._values.items()}


def _update_table(stmt: Update) -> str:
    return stmt.table.name


def _patch_fulfill_common(monkeypatch, *, requests_enabled: bool = True) -> None:
    cfg = fake_scheduler_config(requests_enabled=requests_enabled)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.MiraMediaConfig",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.build_seerr_client",
        lambda: None,
    )
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.resolve_metadata_provider",
        lambda _name: native_provider(),
    )


def _patch_integrity_config(monkeypatch, *, enabled: bool = True) -> None:
    cfg = fake_scheduler_config(integrity_check_enabled=enabled)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity.MiraMediaConfig",
        lambda: cfg,
    )
    monkeypatch.setattr("miramedia.torrents.integrity.MiraMediaConfig", lambda: cfg)
    patch_audit_repository_lookups(monkeypatch)


def _high_water_background_session_factory(
    *,
    episode_rows: list[Any] | None = None,
    movie_rows: list[Any] | None = None,
) -> tuple[Any, list[RecordingSession]]:
    shared_episode_rows = list(episode_rows or [])
    shared_movie_rows = list(movie_rows or [])
    sessions: list[RecordingSession] = []

    @asynccontextmanager
    async def _background_session():
        session = RecordingSession(
            episode_rows=shared_episode_rows,
            movie_rows=shared_movie_rows,
        )
        sessions.append(session)
        yield session

    return _background_session, sessions


def test_fresh_movie_request_marks_downloading_and_downloaded(monkeypatch) -> None:
    request = make_request(
        media_type=MediaType.movie,
        status=RequestStatus.approved,
        external_id="tt1234567",
    )
    movie = make_movie()
    request_service = TrackingRequestService(approved=[request])
    movie_service = FakeMovieService(movie, downloaded=True)
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    auto_download.assert_awaited_once_with(movie.id)
    assert request_service.mark_downloading_ids == [request.id]
    assert request_service.mark_downloaded_ids == [request.id]


def test_show_with_downloaded_episode_marks_downloaded(monkeypatch) -> None:
    show = make_show()
    episode_id = show.seasons[0].episodes[0].id
    request = make_request(
        media_type=MediaType.show,
        status=RequestStatus.approved,
        external_id="tt9999999",
    )
    request_service = TrackingRequestService(approved=[request])
    show_service = FakeShowService(show, downloaded_episodes={episode_id})
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_show_service",
        bg_show_service_factory(show_service),
    )
    monkeypatch.setattr(
        "miramedia.shows.service._try_auto_download_show_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    auto_download.assert_awaited_once_with(show.id)
    assert request_service.mark_downloading_ids == [request.id]
    assert request_service.mark_downloaded_ids == [request.id]


def test_fulfill_request_exception_logged_without_crashing_loop(
    monkeypatch,
    caplog,
) -> None:
    failing = make_request(title="Fails", external_id="tt0000001")
    succeeding = make_request(title="Succeeds", external_id="tt0000002")
    movie = make_movie()
    request_service = TrackingRequestService(approved=[failing, succeeding])
    movie_service = FakeMovieService(
        movie, downloaded=True, add_raises=RuntimeError("boom")
    )
    auto_download = AsyncMock()
    call_count = 0

    async def _add_then_succeed(*, external_id, metadata_provider):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = "boom"
            raise RuntimeError(err)
        movie_service.add_movie_calls.append((external_id, metadata_provider))
        return movie

    movie_service.add_movie = _add_then_succeed  # type: ignore[method-assign]

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    with caplog.at_level("ERROR"):
        _run(scheduler.fulfill_approved_requests_task())

    assert call_count == 2
    assert request_service.mark_downloaded_ids == [succeeding.id]
    assert any(
        "Failed to fulfill request" in record.message for record in caplog.records
    )


def test_verify_chunk_matching_and_mismatching_rows_accounted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    matching_id = uuid.uuid4()
    mismatch_id = uuid.uuid4()
    matching_path = tmp_path / "matching.mkv"
    mismatch_path = tmp_path / "mismatch.mkv"
    matching_path.write_bytes(b"same")
    mismatch_path.write_bytes(b"changed")
    prior_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    mismatch_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    computed_mismatch = "cccccccccccccccccccccccccccccccccccccccc"

    matching_row = FakeFileRow(
        id=matching_id,
        sha1=prior_sha,
        _resolved_path=matching_path,
        movie_id=uuid.uuid4(),
        episode_id=None,
    )
    mismatch_row = FakeFileRow(
        id=mismatch_id,
        sha1=mismatch_sha,
        _resolved_path=mismatch_path,
        movie_id=uuid.uuid4(),
        episode_id=None,
    )
    bg_session, sessions = _high_water_background_session_factory(
        movie_rows=[matching_row, mismatch_row],
    )

    async def _compute_sha1_async(path: Path) -> str | None:
        if path == matching_path:
            return prior_sha
        if path == mismatch_path:
            return computed_mismatch
        return None

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity.background_session",
        bg_session,
    )
    patch_batch_resolve_paths(
        monkeypatch,
        {matching_id: matching_path, mismatch_id: mismatch_path},
    )
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity.compute_sha1_async",
        _compute_sha1_async,
    )

    _run(scheduler.verify_imported_files_task())

    updates = _all_updates(sessions)
    assert len(updates) == 1
    update = updates[0]
    assert _update_table(update) == MovieFile.__tablename__
    values = _update_values(update)
    assert "import_error" in values
    assert "sha1 mismatch" in values["import_error"]


def test_integrity_audit_hashes_with_bounded_concurrency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Integrity audit fans out chunk hashing up to the configured SHA1 cap."""
    concurrency = 2
    num_files = 4
    paths_by_id: dict[uuid.UUID, Path] = {}
    rows: list[FakeFileRow] = []
    expected_sha_by_path: dict[Path, str] = {}

    for i in range(num_files):
        file_id = uuid.uuid4()
        path = tmp_path / f"file-{i}.mkv"
        path.write_bytes(f"content-{i}".encode())
        expected_sha_by_path[path] = f"sha-for-file-{i}"
        paths_by_id[file_id] = path
        rows.append(
            FakeFileRow(
                id=file_id,
                sha1=None,
                _resolved_path=path,
                movie_id=uuid.uuid4(),
                episode_id=None,
            )
        )

    lock = threading.Lock()
    active = 0
    peak = 0
    delays = {i: 0.05 * (num_files - i) for i in range(num_files)}

    def _slow_compute_sha1(path: Path) -> str | None:
        nonlocal active, peak
        file_index = int(path.stem.split("-")[1])
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(delays[file_index])
        with lock:
            active -= 1
        return expected_sha_by_path[path]

    bg_session, sessions = _high_water_background_session_factory(movie_rows=rows)

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity._SHA1_CONCURRENCY",
        concurrency,
    )
    monkeypatch.setattr("miramedia.scheduler_tasks.integrity._SHA1_SEM", None)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity.background_session",
        bg_session,
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.compute_sha1",
        _slow_compute_sha1,
    )
    patch_batch_resolve_paths(monkeypatch, paths_by_id)

    _run(scheduler.verify_imported_files_task())

    assert peak > 1
    assert peak <= concurrency

    updates = _all_updates(sessions)
    assert len(updates) == num_files
    updated_shas = {_update_values(update)["sha1"] for update in updates}
    assert updated_shas == set(expected_sha_by_path.values())


def test_integrity_audit_hash_exception_propagates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    good_id = uuid.uuid4()
    bad_id = uuid.uuid4()
    good_path = tmp_path / "good.mkv"
    bad_path = tmp_path / "bad.mkv"
    good_path.write_bytes(b"good")
    bad_path.write_bytes(b"bad")
    rows = [
        FakeFileRow(
            id=good_id,
            sha1=None,
            _resolved_path=good_path,
            movie_id=uuid.uuid4(),
            episode_id=None,
        ),
        FakeFileRow(
            id=bad_id,
            sha1=None,
            _resolved_path=bad_path,
            movie_id=uuid.uuid4(),
            episode_id=None,
        ),
    ]
    bg_session, _sessions = _high_water_background_session_factory(movie_rows=rows)

    def _raising_compute_sha1(path: Path) -> str | None:
        if path == bad_path:
            err = "disk exploded"
            raise RuntimeError(err)
        return "good-sha"

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity.background_session",
        bg_session,
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.compute_sha1",
        _raising_compute_sha1,
    )
    patch_batch_resolve_paths(
        monkeypatch,
        {good_id: good_path, bad_id: bad_path},
    )

    with pytest.raises(RuntimeError, match="disk exploded"):
        _run(scheduler.verify_imported_files_task())


def test_integrity_audit_hash_cancellation_propagates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    rows: list[FakeFileRow] = []
    paths_by_id: dict[uuid.UUID, Path] = {}
    for i in range(6):
        file_id = uuid.uuid4()
        path = tmp_path / f"slow-{i}.mkv"
        path.write_bytes(b"x")
        paths_by_id[file_id] = path
        rows.append(
            FakeFileRow(
                id=file_id,
                sha1=None,
                _resolved_path=path,
                movie_id=uuid.uuid4(),
                episode_id=None,
            )
        )

    def _slow_compute_sha1(path: Path) -> str | None:  # noqa: ARG001
        time.sleep(0.2)
        return "slow-sha"

    bg_session, _sessions = _high_water_background_session_factory(movie_rows=rows)

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler_tasks.integrity._SHA1_CONCURRENCY", 4)
    monkeypatch.setattr("miramedia.scheduler_tasks.integrity._SHA1_SEM", None)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.integrity.background_session",
        bg_session,
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.compute_sha1",
        _slow_compute_sha1,
    )
    patch_batch_resolve_paths(monkeypatch, paths_by_id)

    async def _run_cancelled() -> None:
        task = asyncio.create_task(scheduler.verify_imported_files_task())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run_cancelled())


def test_cleanup_old_logs_deletes_with_configured_retention(monkeypatch) -> None:
    retention_days = 14
    deleted_cutoff: datetime | None = None
    mock_repo = MagicMock()

    class _Db:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _session_local_background():
        yield _Db()

    def _log_repository(db) -> MagicMock:
        assert db is not None
        return mock_repo

    async def _capture_delete(cutoff: datetime) -> int:
        nonlocal deleted_cutoff
        deleted_cutoff = cutoff
        return 3

    mock_repo.delete_older_than = _capture_delete

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: fake_scheduler_config(log_retention_days=retention_days),
    )
    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_local_background,
    )
    monkeypatch.setattr(
        "miramedia.logs.repository.LogRepository",
        _log_repository,
    )

    before = datetime.now(UTC)
    _run(scheduler.cleanup_old_logs_task())
    after = datetime.now(UTC)

    assert deleted_cutoff is not None
    expected = before - timedelta(days=retention_days)
    assert deleted_cutoff >= expected - timedelta(seconds=2)
    assert deleted_cutoff <= after - timedelta(days=retention_days) + timedelta(
        seconds=2
    )


def test_cleanup_old_notifications_skips_when_native_disabled(monkeypatch) -> None:
    opened = False

    @asynccontextmanager
    async def _fail_session():
        nonlocal opened
        opened = True
        msg = "SessionLocalBackground should not open when notifications disabled"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: fake_scheduler_config(notifications_enabled=False),
    )
    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _fail_session,
    )

    _run(scheduler.cleanup_old_notifications_task())

    assert opened is False


def test_cleanup_old_notifications_deletes_read_with_configured_retention(
    monkeypatch,
) -> None:
    retention_days = 7
    deleted_cutoff: datetime | None = None
    mock_repo = MagicMock()

    class _Db:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _session_local_background():
        yield _Db()

    def _notification_repository(db) -> MagicMock:
        assert db is not None
        return mock_repo

    async def _capture_delete(cutoff: datetime) -> int:
        nonlocal deleted_cutoff
        deleted_cutoff = cutoff
        return 2

    mock_repo.delete_read_older_than = _capture_delete

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: fake_scheduler_config(
            notifications_enabled=True,
            notification_retention_days=retention_days,
        ),
    )
    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_local_background,
    )
    monkeypatch.setattr(
        "miramedia.notifications.repository.NotificationRepository",
        _notification_repository,
    )

    before = datetime.now(UTC)
    _run(scheduler.cleanup_old_notifications_task())
    after = datetime.now(UTC)

    assert deleted_cutoff is not None
    expected = before - timedelta(days=retention_days)
    assert deleted_cutoff >= expected - timedelta(seconds=2)
    assert deleted_cutoff <= after - timedelta(days=retention_days) + timedelta(
        seconds=2
    )

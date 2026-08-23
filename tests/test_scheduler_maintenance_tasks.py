"""Behavioral tests for scheduler maintenance and thin media task bodies (plan 403)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.sql.dml import Delete

from miramedia.indexers.models import IndexerQueryResult
from miramedia.scheduler_tasks import maintenance as maintenance_tasks
from miramedia.scheduler_tasks import media as media_tasks
from tests.fakes.config import fake_scheduler_config


def _run(coro) -> None:
    asyncio.run(coro)


def _delete_cutoff(stmt: Delete) -> datetime:
    for criterion in stmt._where_criteria:
        left = getattr(criterion, "left", None)
        right = getattr(criterion, "right", None)
        if getattr(left, "key", None) == "created_at":
            value = getattr(right, "value", None)
            if value is not None:
                return value
    msg = "DELETE statement missing created_at cutoff"
    raise AssertionError(msg)


@dataclass
class MaintenanceSession:
    """Fake async session recording execute/commit/rollback for maintenance tasks."""

    executes: list[Any] = field(default_factory=list)
    commits: int = 0
    rollbacks: int = 0
    execute_side_effect: Any | None = None

    async def execute(self, stmt: Any, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        self.executes.append(stmt)
        if self.execute_side_effect is not None:
            result = self.execute_side_effect(stmt, len(self.executes))
            if isinstance(result, BaseException):
                raise result
            return result
        return SimpleNamespace(rowcount=2)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _session_factory(
    sessions: list[MaintenanceSession],
    *,
    execute_side_effect: Any | None = None,
) -> Any:
    @asynccontextmanager
    async def _session_local_background():
        session = MaintenanceSession(execute_side_effect=execute_side_effect)
        sessions.append(session)
        yield session

    return _session_local_background


def _maintenance_config(
    *,
    indexer_query_result_retention_days: int = 30,
    notifications_enabled: bool = True,
    notification_retention_days: int = 30,
    hls_cache_max_gb: float = 1.5,
    hls_cache_max_age_days: int = 14,
    image_directory: Path | None = None,
    native_torrents_enabled: bool = True,
    updates_enabled: bool = True,
    subtitles_enabled: bool = True,
    subtitles_native_enabled: bool = True,
    auto_scan_enabled: bool = True,
) -> SimpleNamespace:
    cfg = fake_scheduler_config(
        notifications_enabled=notifications_enabled,
        notification_retention_days=notification_retention_days,
    )
    cfg.misc.indexer_query_result_retention_days = indexer_query_result_retention_days
    cfg.misc.image_directory = image_directory or Path("/fake/images")
    cfg.streams = SimpleNamespace(
        hls_cache_max_gb=hls_cache_max_gb,
        hls_cache_max_age_days=hls_cache_max_age_days,
    )
    cfg.torrents = SimpleNamespace(
        native=SimpleNamespace(enabled=native_torrents_enabled),
    )
    cfg.updates = SimpleNamespace(enabled=updates_enabled, notify_on_new_version=False)
    cfg.subtitles = SimpleNamespace(
        enabled=subtitles_enabled,
        native=SimpleNamespace(enabled=subtitles_native_enabled),
    )
    cfg.imports = SimpleNamespace(auto_scan_enabled=auto_scan_enabled)
    return cfg


def test_purge_old_indexer_query_results_skips_when_retention_zero(monkeypatch) -> None:
    opened = False

    @asynccontextmanager
    async def _fail_session():
        nonlocal opened
        opened = True
        msg = "SessionLocalBackground should not open when retention is zero"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(indexer_query_result_retention_days=0),
    )
    monkeypatch.setattr("miramedia.database.SessionLocalBackground", _fail_session)

    _run(maintenance_tasks.purge_old_indexer_query_results())

    assert opened is False


def test_purge_old_indexer_query_results_deletes_with_configured_cutoff(
    monkeypatch,
) -> None:
    retention_days = 30
    sessions: list[MaintenanceSession] = []

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(
            indexer_query_result_retention_days=retention_days,
        ),
    )
    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_factory(sessions),
    )

    before = datetime.now(UTC)
    _run(maintenance_tasks.purge_old_indexer_query_results())
    after = datetime.now(UTC)

    assert len(sessions) == 1
    session = sessions[0]
    assert session.commits == 1
    assert len(session.executes) == 1
    stmt = session.executes[0]
    assert isinstance(stmt, Delete)
    assert stmt.table.name == IndexerQueryResult.__tablename__
    cutoff = _delete_cutoff(stmt)
    expected = before - timedelta(days=retention_days)
    assert cutoff >= expected - timedelta(seconds=2)
    assert cutoff <= after - timedelta(days=retention_days) + timedelta(seconds=2)


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
        lambda: _maintenance_config(notifications_enabled=False),
    )
    monkeypatch.setattr("miramedia.database.SessionLocalBackground", _fail_session)

    _run(maintenance_tasks.cleanup_old_notifications())

    assert opened is False


def test_cleanup_old_notifications_deletes_read_with_configured_cutoff(
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
        lambda: _maintenance_config(
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
    _run(maintenance_tasks.cleanup_old_notifications())
    after = datetime.now(UTC)

    assert deleted_cutoff is not None
    expected = before - timedelta(days=retention_days)
    assert deleted_cutoff >= expected - timedelta(seconds=2)
    assert deleted_cutoff <= after - timedelta(days=retention_days) + timedelta(
        seconds=2
    )


def test_purge_old_taskiq_messages_deletes_each_configured_table(
    monkeypatch,
) -> None:
    table_names = {
        "taskiq_messages_interactive",
        "taskiq_messages_background",
    }
    sessions: list[MaintenanceSession] = []

    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_factory(sessions),
    )

    _run(maintenance_tasks.purge_old_taskiq_messages(taskiq_table_names=table_names))

    assert len(sessions) == 1
    session = sessions[0]
    assert session.commits == 2
    assert session.rollbacks == 0
    sql_texts = [str(stmt) for stmt in session.executes]
    assert len(sql_texts) == 2
    for table_name in table_names:
        assert any(table_name in sql for sql in sql_texts)
    for sql in sql_texts:
        assert "DELETE FROM" in sql
        assert "INTERVAL '7 days'" in sql


def test_purge_old_taskiq_messages_continues_after_table_failure(
    monkeypatch,
) -> None:
    failing_table = "taskiq_messages_interactive"
    succeeding_table = "taskiq_messages_background"

    def _execute_side_effect(stmt: Any, _call_index: int) -> Any:
        if failing_table in str(stmt):
            return RuntimeError("purge failed")
        return SimpleNamespace(rowcount=1)

    sessions: list[MaintenanceSession] = []
    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_factory(sessions, execute_side_effect=_execute_side_effect),
    )

    _run(
        maintenance_tasks.purge_old_taskiq_messages(
            taskiq_table_names={failing_table, succeeding_table},
        )
    )

    assert len(sessions) == 1
    session = sessions[0]
    assert session.rollbacks == 1
    assert session.commits == 1
    sql_texts = [str(stmt) for stmt in session.executes]
    assert any(failing_table in sql for sql in sql_texts)
    assert any(succeeding_table in sql for sql in sql_texts)


def test_cleanup_hls_cache_skips_when_max_bytes_zero(monkeypatch) -> None:
    sweep = MagicMock()

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(hls_cache_max_gb=0, hls_cache_max_age_days=7),
    )
    monkeypatch.setattr(
        "miramedia.streams.transcode.sweep_hls_cache",
        sweep,
    )

    _run(maintenance_tasks.cleanup_hls_cache())

    sweep.assert_not_called()


def test_cleanup_hls_cache_skips_when_max_age_zero(monkeypatch) -> None:
    sweep = MagicMock()

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(hls_cache_max_gb=2.0, hls_cache_max_age_days=0),
    )
    monkeypatch.setattr(
        "miramedia.streams.transcode.sweep_hls_cache",
        sweep,
    )

    _run(maintenance_tasks.cleanup_hls_cache())

    sweep.assert_not_called()


def test_cleanup_hls_cache_converts_config_to_bytes_and_seconds(monkeypatch) -> None:
    max_gb = 1.5
    max_age_days = 10
    sweep = MagicMock(
        return_value={"deleted_dirs": 0, "freed_bytes": 0, "remaining_bytes": 0},
    )

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(
            hls_cache_max_gb=max_gb,
            hls_cache_max_age_days=max_age_days,
        ),
    )
    monkeypatch.setattr(
        "miramedia.streams.transcode.sweep_hls_cache",
        sweep,
    )

    _run(maintenance_tasks.cleanup_hls_cache())

    sweep.assert_called_once_with(int(max_gb * 1024**3), max_age_days * 86400)


def test_cleanup_poster_variants_calls_evict_with_configured_dirs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    evict = MagicMock(return_value=[])

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(image_directory=image_dir),
    )
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.evict_poster_variants",
        evict,
    )

    _run(maintenance_tasks.cleanup_poster_variants())

    evict.assert_called_once_with(image_dir, image_dir / ".variants")


def test_save_native_resume_data_skips_when_native_disabled(monkeypatch) -> None:
    client_ctor = MagicMock()

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(native_torrents_enabled=False),
    )
    monkeypatch.setattr(
        "miramedia.torrents.backends.native.NativeDownloadClient",
        client_ctor,
    )

    _run(maintenance_tasks.save_native_resume_data())

    client_ctor.assert_not_called()


def test_save_native_resume_data_invokes_client_via_thread(monkeypatch) -> None:
    save_resume_data = MagicMock()
    client = MagicMock(save_resume_data=save_resume_data)
    client_ctor = MagicMock(return_value=client)

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.maintenance.MiraMediaConfig",
        lambda: _maintenance_config(native_torrents_enabled=True),
    )
    monkeypatch.setattr(
        "miramedia.torrents.backends.native.NativeDownloadClient",
        client_ctor,
    )

    _run(maintenance_tasks.save_native_resume_data())

    client_ctor.assert_called_once_with()
    save_resume_data.assert_called_once_with()


def test_update_all_movies_metadata_awaits_impl_once(monkeypatch) -> None:
    metadata_impl = AsyncMock()
    auto_download_impl = AsyncMock()

    monkeypatch.setattr(
        "miramedia.movies.service._update_all_movies_metadata_impl",
        metadata_impl,
    )
    monkeypatch.setattr(
        "miramedia.movies.service._auto_download_missing_movies_impl",
        auto_download_impl,
    )

    _run(media_tasks.update_all_movies_metadata())

    metadata_impl.assert_awaited_once_with()
    auto_download_impl.assert_awaited_once_with()


def test_update_all_shows_metadata_awaits_impl_once(monkeypatch) -> None:
    metadata_impl = AsyncMock()
    auto_download_impl = AsyncMock()

    monkeypatch.setattr(
        "miramedia.shows.service._update_all_shows_metadata_impl",
        metadata_impl,
    )
    monkeypatch.setattr(
        "miramedia.shows.service._auto_download_missing_episodes_impl",
        auto_download_impl,
    )

    _run(media_tasks.update_all_shows_metadata())

    metadata_impl.assert_awaited_once_with()
    auto_download_impl.assert_awaited_once_with()


def test_scheduled_library_scan_awaits_scan_when_enabled(monkeypatch) -> None:
    scan_and_cache = AsyncMock()

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.MiraMediaConfig",
        lambda: _maintenance_config(auto_scan_enabled=True),
    )
    monkeypatch.setattr(
        "miramedia.imports.tasks._scan_and_cache",
        scan_and_cache,
    )

    _run(media_tasks.scheduled_library_scan())

    scan_and_cache.assert_awaited_once_with()


def test_scheduled_library_scan_skips_when_disabled(monkeypatch) -> None:
    scan_and_cache = AsyncMock()

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.MiraMediaConfig",
        lambda: _maintenance_config(auto_scan_enabled=False),
    )
    monkeypatch.setattr(
        "miramedia.imports.tasks._scan_and_cache",
        scan_and_cache,
    )

    _run(media_tasks.scheduled_library_scan())

    scan_and_cache.assert_not_awaited()


def test_scan_missing_subtitles_awaits_service_when_enabled(monkeypatch) -> None:
    scan_all = AsyncMock()
    subtitle_service = MagicMock()
    subtitle_service.scan_all_missing_subtitles = scan_all

    @asynccontextmanager
    async def _bg_subtitle_service():
        yield subtitle_service

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.MiraMediaConfig",
        lambda: _maintenance_config(
            subtitles_enabled=True,
            subtitles_native_enabled=True,
        ),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_subtitle_service",
        _bg_subtitle_service,
    )

    _run(media_tasks.scan_missing_subtitles())

    scan_all.assert_awaited_once_with()


def test_check_for_updates_queries_service_when_enabled(monkeypatch) -> None:
    get_update_info = MagicMock(
        return_value=SimpleNamespace(
            update_available=False,
            current_version="1.0.0",
            latest_version="1.0.0",
            release_url=None,
        ),
    )
    service = MagicMock(get_update_info=get_update_info)
    service_ctor = MagicMock(return_value=service)

    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.MiraMediaConfig",
        lambda: _maintenance_config(updates_enabled=True),
    )
    monkeypatch.setattr(
        "miramedia.updates.service.UpdateService",
        service_ctor,
    )

    _run(media_tasks.check_for_updates())

    service_ctor.assert_called_once_with()
    get_update_info.assert_called_once_with(True)


def test_cleanup_expired_manual_parse_tokens_deletes_via_repository(
    monkeypatch,
) -> None:
    mock_repo = MagicMock()
    deleted_count = 0

    class _Db:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _session_local_background():
        yield _Db()

    async def _delete_expired(ttl_minutes: int) -> int:
        nonlocal deleted_count
        assert ttl_minutes == 30
        deleted_count = 4
        return deleted_count

    mock_repo.delete_expired_manual_parse_tokens = _delete_expired

    def _torrent_repository(db) -> MagicMock:
        assert db is not None
        return mock_repo

    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_local_background,
    )
    monkeypatch.setattr(
        "miramedia.torrents.repository.TorrentRepository",
        _torrent_repository,
    )

    _run(maintenance_tasks.cleanup_expired_manual_parse_tokens())

    assert deleted_count == 4

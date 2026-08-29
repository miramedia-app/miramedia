"""DB-free diagnostics collectors: database snapshot and scheduled-task catalog."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from miramedia.database.config import DbConfig
from miramedia.diagnostics.database import get_database_diagnostics
from miramedia.diagnostics.scheduler import get_scheduler_diagnostics, task_display_name
from tests.fakes.db import FakeDb


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Cfg:
    database: DbConfig


@dataclass
class _MappingResult:
    rows: list[dict[str, Any]]

    def mappings(self) -> _MappingResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


def _sql_text(stmt: object) -> str:
    raw = getattr(stmt, "text", None)
    return raw if isinstance(raw, str) else str(stmt)


STARTED_AT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

IDENTITY_ROW: dict[str, Any] = {
    "server_version": "17.4 (Debian)",
    "size_bytes": 4096,
    "max_connections": 100,
    "started_at": STARTED_AT,
}

CONNECTION_ROWS: list[dict[str, Any]] = [
    {"state": "active", "count": 3},
    {"state": "idle", "count": 7},
]

TABLE_ROWS: list[dict[str, Any]] = [
    {
        "name": "episode_file",
        "total_bytes": 1_048_576,
        "table_bytes": 786_432,
        "index_bytes": 262_144,
        "estimated_rows": 42,
    }
]


@dataclass
class _PopulatedFakeDb(FakeDb):
    """Returns PostgreSQL-shaped mapping rows for diagnostics catalog SQL."""

    identity: dict[str, Any] = field(default_factory=lambda: dict(IDENTITY_ROW))
    connections: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(row) for row in CONNECTION_ROWS]
    )
    tables: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(row) for row in TABLE_ROWS]
    )

    async def execute(
        self, stmt: object, *_args: object, **_kwargs: object
    ) -> _MappingResult:
        sql = _sql_text(stmt).lower()
        if "pg_stat_activity" in sql:
            return _MappingResult(self.connections)
        if "pg_class" in sql:
            return _MappingResult(self.tables)
        if "server_version" in sql:
            return _MappingResult([self.identity])
        return _MappingResult([])


class _FakePool:
    def status(self) -> str:
        return "Pool size: 5  Connections in pool: 4  Current Overflow: -1"

    def size(self) -> int:
        return 5

    def checkedout(self) -> int:
        return 1

    def overflow(self) -> int:
        return -1


class _FakeEngine:
    pool = _FakePool()


def _install_fake_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine()
    monkeypatch.setattr("miramedia.diagnostics.database.get_engine", lambda: engine)
    monkeypatch.setattr("miramedia.diagnostics.database.background_engine", engine)


def _secret_cfg() -> _Cfg:
    return _Cfg(
        database=DbConfig(
            host="db.internal",
            port=5433,
            user="mira",
            password="super-secret",
            dbname="mira_prod",
        )
    )


def test_task_display_name_strips_module_and_suffix() -> None:
    assert (
        task_display_name("miramedia.scheduler:verify_imported_files_task")
        == "verify imported files"
    )


def test_database_snapshot_uses_config_and_omits_secret() -> None:
    cfg = _secret_cfg()
    snap = _run(get_database_diagnostics(FakeDb(), config=cfg))  # type: ignore[arg-type]
    assert snap.host == "db.internal"
    assert snap.port == 5433
    assert snap.user == "mira"
    assert snap.name == "mira_prod"
    dumped = snap.model_dump()
    assert "password" not in dumped
    assert "super-secret" not in str(dumped)


def test_database_snapshot_maps_postgresql_identity_connections_and_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_pools(monkeypatch)
    snap = _run(
        get_database_diagnostics(_PopulatedFakeDb(), config=_secret_cfg())  # type: ignore[arg-type]
    )
    assert snap.server_version == "17.4 (Debian)"
    assert snap.size_bytes == 4096
    assert snap.max_connections == 100
    assert snap.started_at == STARTED_AT
    assert [(row.state, row.count) for row in snap.connections] == [
        ("active", 3),
        ("idle", 7),
    ]
    assert len(snap.largest_tables) == 1
    table = snap.largest_tables[0]
    assert table.name == "episode_file"
    assert table.total_bytes == 1_048_576
    assert table.table_bytes == 786_432
    assert table.index_bytes == 262_144
    assert table.estimated_rows == 42
    assert {pool.name for pool in snap.pools} == {"request", "background"}
    request = next(pool for pool in snap.pools if pool.name == "request")
    assert request.size == 5
    assert request.checked_out == 1
    assert request.overflow == -1
    assert "Pool size: 5" in request.status
    dumped = snap.model_dump()
    assert "password" not in dumped
    snapshot = str(dumped)
    assert "super-secret" not in snapshot
    assert "password" not in snapshot


def test_scheduler_catalog_includes_cron_tasks_without_db_rows() -> None:
    snap = _run(get_scheduler_diagnostics(FakeDb()))  # type: ignore[arg-type]
    names = {task.task_name for task in snap.tasks}
    assert "miramedia.scheduler:verify_imported_files_task" in names
    assert "miramedia.scheduler:cleanup_old_logs_task" in names
    assert "miramedia.scheduler:add_show_task" not in names
    assert snap.schedules_loaded is False
    assert snap.queue_background is None
    verify = next(
        task
        for task in snap.tasks
        if task.task_name.endswith("verify_imported_files_task")
    )
    assert verify.cron
    assert verify.broker == "background"
    assert verify.queued is None
    assert snap.generated_at.tzinfo is UTC
    assert isinstance(snap.generated_at, datetime)

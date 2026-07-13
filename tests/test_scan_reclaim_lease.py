"""Adversarial tests for scan reclaim CAS, worker lease heartbeat, and targets."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from miramedia.imports.repository import (
    BEGIN_MANUAL_SCAN_WORKER_SQL,
    CLAIM_SCAN_CACHE_ROW_SQL,
    COMPLETE_MANUAL_SCAN_IMPORT_SQL,
    FAIL_MANUAL_SCAN_IMPORT_SQL,
    HEARTBEAT_MANUAL_SCAN_WORKER_SQL,
    RECLAIM_STALE_QUEUED_IMPORT_SQL,
    RESET_IMPORT_BATCH_IF_IDLE_SQL,
    SELECT_QUEUED_IMPORT_SNAPSHOT_SQL,
    ImportsRepository,
    ScanClaimResult,
    ScanWorkerBeginResult,
)
from miramedia.imports.scan_lease import (
    STALE_QUEUED_IMPORT_GRACE,
    ScanWorkerLease,
    ScanWorkerLeaseHeartbeat,
)
from miramedia.imports.scan_resolve import validate_scan_resolve_target
from miramedia.imports.schemas import ResolveRequest

PREFIX = "/api/v1/imports"
_OLD = "2026-07-13T08:00:00+00:00"
_FRESH = "2026-07-13T10:00:00+00:00"


def _patch_repo_now(fixed: datetime):
    return patch("miramedia.imports.repository._utc_now", return_value=fixed)


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _SnapshotRow:
    def __init__(
        self,
        directory: str,
        claim_token: str | None,
        queued_at: str | None,
        worker_started_at: str | None,
    ) -> None:
        self.directory = directory
        self.claim_token = claim_token
        self.queued_at = queued_at
        self.worker_started_at = worker_started_at


def _sql_text(stmt: object) -> str:
    if isinstance(stmt, TextClause):
        return stmt.text
    return str(stmt)


class _StatefulScanCache:
    """Minimal in-memory executor for scan-cache CAS SQL used in reclaim tests."""

    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = {directory: dict(payload) for directory, payload in rows.items()}
        self.batch_total = 0

    def snapshot_rows(self) -> list[_SnapshotRow]:
        out: list[_SnapshotRow] = []
        for directory, payload in self.rows.items():
            if payload.get("status") != "queued":
                continue
            out.append(
                _SnapshotRow(
                    directory=directory,
                    claim_token=payload.get("claim_token"),  # type: ignore[arg-type]
                    queued_at=payload.get("queued_at"),  # type: ignore[arg-type]
                    worker_started_at=payload.get("worker_started_at"),  # type: ignore[arg-type]
                )
            )
        return out

    def execute(
        self, stmt: object, params: dict[str, Any] | None = None
    ) -> _FakeResult:
        params = params or {}
        sql = _sql_text(stmt).strip()

        if sql == SELECT_QUEUED_IMPORT_SNAPSHOT_SQL.strip():
            return _FakeResult(self.snapshot_rows())

        if sql == RESET_IMPORT_BATCH_IF_IDLE_SQL.strip():
            if not any(p.get("status") == "queued" for p in self.rows.values()):
                self.batch_total = 0
            return _FakeResult([])

        directory = params["directory"]
        payload = self.rows.get(directory)
        if payload is None:
            return _FakeResult([])

        if sql == BEGIN_MANUAL_SCAN_WORKER_SQL.strip():
            if (
                payload.get("status") == "queued"
                and payload.get("media_type_hint") == params["media_type"]
                and payload.get("claim_token") == params["claim_token"]
                and payload.get("worker_started_at") is None
            ):
                payload["worker_started_at"] = params["worker_started_at"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == HEARTBEAT_MANUAL_SCAN_WORKER_SQL.strip():
            if (
                payload.get("status") == "queued"
                and payload.get("media_type_hint") == params["media_type"]
                and payload.get("claim_token") == params["claim_token"]
                and payload.get("worker_started_at")
                == params["expected_worker_started_at"]
            ):
                payload["worker_started_at"] = params["worker_started_at"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == RECLAIM_STALE_QUEUED_IMPORT_SQL.strip():
            queued_at = payload.get("queued_at")
            started_at = payload.get("worker_started_at")
            expected_queued = params["expected_queued_at"]
            expected_started = params["expected_worker_started_at"]
            queued_match = (expected_queued is None and queued_at is None) or (
                queued_at == expected_queued
            )
            started_match = (expected_started is None and started_at is None) or (
                started_at == expected_started
            )
            if (
                payload.get("status") == "queued"
                and payload.get("claim_token") == params["claim_token"]
                and queued_match
                and started_match
            ):
                payload.pop("queued_at", None)
                payload.pop("claim_token", None)
                payload.pop("worker_started_at", None)
                payload["status"] = "failed"
                payload["import_error"] = params["error"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == CLAIM_SCAN_CACHE_ROW_SQL.strip():
            if (
                payload.get("status") in {"pending", "failed"}
                and payload.get("media_type_hint") == params["media_type"]
            ):
                payload.pop("import_error", None)
                payload.pop("claim_token", None)
                payload.pop("worker_started_at", None)
                payload["status"] = "queued"
                payload["queued_at"] = params["queued_at"]
                payload["claim_token"] = params["claim_token"]
                self.batch_total += 1
                return _FakeResult([(directory,)])
            return _FakeResult([])

        msg = f"unexpected SQL in stateful fake: {sql[:80]}"
        raise AssertionError(msg)


def _repo_with_state(state: _StatefulScanCache) -> ImportsRepository:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=state.execute)
    db.scalar = AsyncMock(side_effect=lambda *_a, **_k: next(iter(state.rows), None))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return ImportsRepository(db=db)


def _scan_body(**overrides: object) -> dict[str, object]:
    body = {
        "kind": "scan",
        "id": "/safe/show",
        "media_type": "show",
        "media_id": str(uuid.uuid4()),
    }
    body.update(overrides)
    return body


def test_validate_scan_resolve_target_rejects_missing_target() -> None:
    body = ResolveRequest.model_validate(_scan_body(media_id=None))
    with pytest.raises(HTTPException) as exc_info:
        validate_scan_resolve_target(body)
    assert exc_info.value.status_code == 400


def test_validate_scan_resolve_target_rejects_ambiguous_both() -> None:
    body = ResolveRequest.model_validate(
        _scan_body(
            external_id="tv-123",
            metadata_provider="tvmaze",
        )
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_scan_resolve_target(body)
    assert exc_info.value.status_code == 400


def test_validate_scan_resolve_target_rejects_incomplete_provider_pair() -> None:
    body = ResolveRequest.model_validate(
        _scan_body(media_id=None, external_id="tv-123", metadata_provider=None)
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_scan_resolve_target(body)
    assert exc_info.value.status_code == 400


def test_invalid_scan_target_returns_400_without_claim_or_dispatch() -> None:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.imports.dependencies import get_imports_repository
    from miramedia.main import app

    repo = MagicMock()
    repo.claim_scan_cache_row = AsyncMock()
    repo.get_scan_cache_entry = AsyncMock()

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.is_superuser = True
        return user

    async def _repo_dep() -> Any:
        return repo

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_imports_repository] = _repo_dep

    task_mock = AsyncMock()
    with patch("miramedia.imports.tasks.resolve_import_task") as task_module:
        task_module.kiq = task_mock
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                f"{PREFIX}/resolve",
                json=_scan_body(media_id=None),
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 400
    repo.claim_scan_cache_row.assert_not_awaited()
    task_mock.assert_not_awaited()


def test_reclaim_interleaving_worker_begin_blocks_stale_snapshot_cas() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "token-a",
                "queued_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)
    stale_candidate = {
        "directory": directory,
        "claim_token": "token-a",
        "expected_queued_at": _OLD,
        "expected_worker_started_at": None,
        "error": "stale snapshot",
    }

    async def _run() -> None:
        with _patch_repo_now(datetime.fromisoformat(_FRESH)):
            began = await repo.begin_manual_scan_worker(
                directory, claim_token="token-a", media_type="show"
            )
            assert began.result is ScanWorkerBeginResult.started
            assert state.rows[directory]["worker_started_at"] is not None

            lost = state.execute(
                text(RECLAIM_STALE_QUEUED_IMPORT_SQL),
                stale_candidate,
            )
            assert lost.first() is None
            assert state.rows[directory]["status"] == "queued"
            assert state.rows[directory]["claim_token"] == "token-a"

            claim = await repo.claim_scan_cache_row(directory, media_type="show")
            assert claim.result is ScanClaimResult.not_eligible

    asyncio.run(_run())


def test_heartbeat_refreshes_lease_and_blocks_reclaim_until_expired() -> None:
    directory = "/safe/show"
    now = datetime.fromisoformat(_FRESH)
    started_at = (now - timedelta(minutes=10)).isoformat()
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "token-a",
                "queued_at": _OLD,
                "worker_started_at": started_at,
            }
        }
    )
    repo = _repo_with_state(state)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(now),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
            assert reclaimed == 0

        refreshed = now.isoformat()
        ok = await repo.heartbeat_manual_scan_worker(
            directory,
            claim_token="token-a",
            media_type="show",
            expected_worker_started_at=started_at,
            worker_started_at=refreshed,
        )
        assert ok is True
        assert state.rows[directory]["worker_started_at"] == refreshed

        expired_now = now + STALE_QUEUED_IMPORT_GRACE + timedelta(minutes=1)
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(expired_now),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        assert state.rows[directory]["status"] == "failed"
        assert "claim_token" not in state.rows[directory]

    asyncio.run(_run())


def test_heartbeat_pulse_uses_independent_session() -> None:
    directory = "/safe/show"
    repo = MagicMock()
    repo.heartbeat_manual_scan_worker = AsyncMock(return_value=True)
    sessions: list[MagicMock] = []

    @asynccontextmanager
    async def _session_factory():
        db = MagicMock()
        sessions.append(db)
        yield db

    lease = ScanWorkerLease(
        directory=directory,
        claim_token="token-a",
        media_type="show",
        worker_started_at=_OLD,
    )
    heartbeat = ScanWorkerLeaseHeartbeat(
        lease,
        session_factory=_session_factory,
        now=lambda: datetime.fromisoformat(_FRESH),
    )

    async def _run() -> None:
        with patch(
            "miramedia.imports.repository.ImportsRepository",
            return_value=repo,
        ):
            ok = await heartbeat.pulse()

        assert ok is True
        assert len(sessions) == 1
        repo.heartbeat_manual_scan_worker.assert_awaited_once()

    asyncio.run(_run())


def test_complete_terminal_and_batch_reset_share_one_commit() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            _FakeResult([]),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = ImportsRepository(db=db)

    async def _run() -> None:
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            ok = await repo.complete_manual_scan_import(
                "/safe/show",
                claim_token="token-a",
                imported_name="Show",
                imported_media_id=str(uuid.uuid4()),
                imported_media_type="show",
            )

        assert ok is True
        assert db.execute.await_count == 2
        assert COMPLETE_MANUAL_SCAN_IMPORT_SQL.strip() in _sql_text(
            db.execute.await_args_list[0].args[0]
        )
        assert RESET_IMPORT_BATCH_IF_IDLE_SQL.strip() in _sql_text(
            db.execute.await_args_list[1].args[0]
        )
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()

    asyncio.run(_run())


def test_complete_rolls_back_when_batch_reset_fails() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            RuntimeError("batch reset failed"),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = ImportsRepository(db=db)

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="batch reset failed"):
            await repo.complete_manual_scan_import(
                "/safe/show",
                claim_token="token-a",
            )

        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    asyncio.run(_run())


def test_fail_terminal_and_batch_reset_share_one_commit() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            _FakeResult([]),
        ]
    )
    db.commit = AsyncMock()
    repo = ImportsRepository(db=db)

    async def _run() -> None:
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            ok = await repo.fail_manual_scan_import(
                "/safe/show", claim_token="token-a", error="boom"
            )

        assert ok is True
        assert FAIL_MANUAL_SCAN_IMPORT_SQL.strip() in _sql_text(
            db.execute.await_args_list[0].args[0]
        )
        assert RESET_IMPORT_BATCH_IF_IDLE_SQL.strip() in _sql_text(
            db.execute.await_args_list[1].args[0]
        )
        db.commit.assert_awaited_once()

    asyncio.run(_run())


def test_reclaim_resets_batch_in_same_commit_and_rolls_back_on_error() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
                "queued_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)

    async def _run_success() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        repo.db.commit.assert_awaited_once()

    asyncio.run(_run_success())

    state.rows[directory] = {
        "status": "queued",
        "claim_token": "token-a",
        "queued_at": _OLD,
    }
    original_execute = state.execute

    def _execute_with_reset_failure(
        stmt: object, params: dict[str, Any] | None = None
    ) -> _FakeResult:
        sql = _sql_text(stmt).strip()
        if sql == RESET_IMPORT_BATCH_IF_IDLE_SQL.strip():
            reset_failed = "reset failed"
            raise RuntimeError(reset_failed)
        return original_execute(stmt, params)

    repo.db.execute = AsyncMock(side_effect=_execute_with_reset_failure)
    repo.db.commit = AsyncMock()
    repo.db.rollback = AsyncMock()

    async def _run_failure() -> None:
        with (
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
            pytest.raises(RuntimeError, match="reset failed"),
        ):
            await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        repo.db.rollback.assert_awaited_once()
        repo.db.commit.assert_not_awaited()

    asyncio.run(_run_failure())

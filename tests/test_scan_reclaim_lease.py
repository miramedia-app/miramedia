"""Adversarial tests for pre-start reclaim CAS and scan resolve targets."""

from __future__ import annotations

import asyncio
import uuid
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
    COMPENSATE_SCAN_CACHE_CLAIM_SQL,
    COMPLETE_MANUAL_SCAN_IMPORT_SQL,
    RECLAIM_STALE_QUEUED_IMPORT_BATCH_SQL,
    RECLAIM_STALE_QUEUED_IMPORT_SQL,
    RECLAIM_STALLED_WORKER_IMPORT_BATCH_SQL,
    RECLAIM_STALLED_WORKER_IMPORT_SQL,
    RESET_IMPORT_BATCH_IF_IDLE_SQL,
    SELECT_QUEUED_IMPORT_SNAPSHOT_SQL,
    SELECT_STARTED_IMPORT_SNAPSHOT_SQL,
    STALE_QUEUED_IMPORT_GRACE,
    STALLED_WORKER_GRACE,
    STAMP_LEGACY_QUEUED_AT_BATCH_SQL,
    STAMP_LEGACY_QUEUED_AT_SQL,
    STAMP_LEGACY_WORKER_STARTED_AT_BATCH_SQL,
    STAMP_LEGACY_WORKER_STARTED_AT_SQL,
    ImportsRepository,
    ScanClaimResult,
    ScanWorkerBeginResult,
    _build_values_from_clause,
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

    def all(self) -> list[object]:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _SnapshotRow:
    def __init__(
        self,
        directory: str,
        claim_token: str | None,
        queued_at: str | None,
    ) -> None:
        self.directory = directory
        self.claim_token = claim_token
        self.queued_at = queued_at


class _StartedSnapshotRow:
    def __init__(
        self,
        directory: str,
        claim_token: str | None,
        worker_started_at: str | None,
    ) -> None:
        self.directory = directory
        self.claim_token = claim_token
        self.worker_started_at = worker_started_at


def _sql_text(stmt: object) -> str:
    if isinstance(stmt, TextClause):
        return stmt.text
    return str(stmt)


def _batch_param_rows(
    params: dict[str, Any], prefix: str, fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    indices = {
        int(key.rsplit("_", 1)[-1])
        for key in params
        if key.startswith(f"{prefix}_{fields[0]}_")
    }
    return [
        {field: params[f"{prefix}_{field}_{i}"] for field in fields}
        for i in sorted(indices)
    ]


def _batch_sql_head(template: str) -> str:
    return template.split("{values_from}")[0].strip()


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
            if payload.get("worker_started_at") is not None:
                continue
            out.append(
                _SnapshotRow(
                    directory=directory,
                    claim_token=payload.get("claim_token"),  # type: ignore[arg-type]
                    queued_at=payload.get("queued_at"),  # type: ignore[arg-type]
                )
            )
        return out

    def started_snapshot_rows(self) -> list[_StartedSnapshotRow]:
        out: list[_StartedSnapshotRow] = []
        for directory, payload in self.rows.items():
            if payload.get("status") != "queued":
                continue
            worker_started_at = payload.get("worker_started_at")
            if worker_started_at is None:
                continue
            out.append(
                _StartedSnapshotRow(
                    directory=directory,
                    claim_token=payload.get("claim_token"),  # type: ignore[arg-type]
                    worker_started_at=worker_started_at,  # type: ignore[arg-type]
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

        if sql == SELECT_STARTED_IMPORT_SNAPSHOT_SQL.strip():
            return _FakeResult(self.started_snapshot_rows())

        if sql == RESET_IMPORT_BATCH_IF_IDLE_SQL.strip():
            if not any(p.get("status") == "queued" for p in self.rows.values()):
                self.batch_total = 0
            return _FakeResult([])

        if "UPDATE import_batch SET total = GREATEST" in sql:
            n = (params or {}).get("n", 1)
            self.batch_total = max(self.batch_total - n, 0)
            return _FakeResult([])

        if sql.startswith(_batch_sql_head(STAMP_LEGACY_QUEUED_AT_BATCH_SQL)):
            stamped: list[tuple[str]] = []
            for row in _batch_param_rows(
                params, "stamp_queued", ("directory", "expected_queued_at")
            ):
                directory = row["directory"]
                row_payload = self.rows.get(directory)
                if row_payload is None:
                    continue
                queued_at = row_payload.get("queued_at")
                expected = row["expected_queued_at"]
                queued_match = (expected is None and queued_at is None) or (
                    queued_at == expected
                )
                if (
                    row_payload.get("status") == "queued"
                    and row_payload.get("worker_started_at") is None
                    and queued_match
                ):
                    row_payload["queued_at"] = params["queued_at"]
                    stamped.append((directory,))
            return _FakeResult(stamped)

        if "v.expected_queued_at" in sql and "worker_started_at' IS NULL" in sql:
            reclaimed_rows: list[tuple[str]] = []
            for row in _batch_param_rows(
                params,
                "reclaim_queued",
                ("directory", "expected_claim_token", "expected_queued_at"),
            ):
                directory = row["directory"]
                row_payload = self.rows.get(directory)
                if row_payload is None:
                    continue
                queued_at = row_payload.get("queued_at")
                claim_token = row_payload.get("claim_token")
                expected_queued = row["expected_queued_at"]
                expected_token = row["expected_claim_token"]
                token_match = (expected_token is None and claim_token is None) or (
                    claim_token == expected_token
                )
                if (
                    row_payload.get("status") == "queued"
                    and row_payload.get("worker_started_at") is None
                    and queued_at == expected_queued
                    and token_match
                ):
                    row_payload.pop("queued_at", None)
                    row_payload.pop("claim_token", None)
                    row_payload.pop("worker_started_at", None)
                    row_payload["status"] = "failed"
                    row_payload["import_error"] = params["error"]
                    reclaimed_rows.append((directory,))
            return _FakeResult(reclaimed_rows)

        if sql.startswith(_batch_sql_head(STAMP_LEGACY_WORKER_STARTED_AT_BATCH_SQL)):
            stamped_worker: list[tuple[str]] = []
            for row in _batch_param_rows(
                params, "stamp_worker", ("directory", "expected_worker_started_at")
            ):
                directory = row["directory"]
                row_payload = self.rows.get(directory)
                if row_payload is None:
                    continue
                worker_started_at = row_payload.get("worker_started_at")
                expected = row["expected_worker_started_at"]
                started_match = (expected is None and worker_started_at is None) or (
                    worker_started_at == expected
                )
                if (
                    row_payload.get("status") == "queued"
                    and worker_started_at is not None
                    and started_match
                ):
                    row_payload["worker_started_at"] = params["worker_started_at"]
                    stamped_worker.append((directory,))
            return _FakeResult(stamped_worker)

        if "v.expected_worker_started_at" in sql:
            reclaimed_worker_rows: list[tuple[str]] = []
            for row in _batch_param_rows(
                params,
                "reclaim_worker",
                ("directory", "expected_claim_token", "expected_worker_started_at"),
            ):
                directory = row["directory"]
                row_payload = self.rows.get(directory)
                if row_payload is None:
                    continue
                worker_started_at = row_payload.get("worker_started_at")
                claim_token = row_payload.get("claim_token")
                expected_started = row["expected_worker_started_at"]
                expected_token = row["expected_claim_token"]
                token_match = (expected_token is None and claim_token is None) or (
                    claim_token == expected_token
                )
                if (
                    row_payload.get("status") == "queued"
                    and worker_started_at == expected_started
                    and token_match
                ):
                    row_payload.pop("queued_at", None)
                    row_payload.pop("claim_token", None)
                    row_payload.pop("worker_started_at", None)
                    row_payload["status"] = "failed"
                    row_payload["import_error"] = params["error"]
                    reclaimed_worker_rows.append((directory,))
            return _FakeResult(reclaimed_worker_rows)

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

        if sql == STAMP_LEGACY_QUEUED_AT_SQL.strip():
            queued_at = payload.get("queued_at")
            expected = params["expected_queued_at"]
            queued_match = (expected is None and queued_at is None) or (
                queued_at == expected
            )
            if (
                payload.get("status") == "queued"
                and payload.get("worker_started_at") is None
                and queued_match
            ):
                payload["queued_at"] = params["queued_at"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == RECLAIM_STALE_QUEUED_IMPORT_SQL.strip():
            queued_at = payload.get("queued_at")
            claim_token = payload.get("claim_token")
            expected_queued = params["expected_queued_at"]
            expected_token = params["expected_claim_token"]
            token_match = (expected_token is None and claim_token is None) or (
                claim_token == expected_token
            )
            if (
                payload.get("status") == "queued"
                and payload.get("worker_started_at") is None
                and queued_at == expected_queued
                and token_match
            ):
                payload.pop("queued_at", None)
                payload.pop("claim_token", None)
                payload.pop("worker_started_at", None)
                payload["status"] = "failed"
                payload["import_error"] = params["error"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == STAMP_LEGACY_WORKER_STARTED_AT_SQL.strip():
            worker_started_at = payload.get("worker_started_at")
            expected = params["expected_worker_started_at"]
            started_match = (expected is None and worker_started_at is None) or (
                worker_started_at == expected
            )
            if (
                payload.get("status") == "queued"
                and worker_started_at is not None
                and started_match
            ):
                payload["worker_started_at"] = params["worker_started_at"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == RECLAIM_STALLED_WORKER_IMPORT_SQL.strip():
            worker_started_at = payload.get("worker_started_at")
            claim_token = payload.get("claim_token")
            expected_started = params["expected_worker_started_at"]
            expected_token = params["expected_claim_token"]
            token_match = (expected_token is None and claim_token is None) or (
                claim_token == expected_token
            )
            if (
                payload.get("status") == "queued"
                and worker_started_at == expected_started
                and token_match
            ):
                payload.pop("queued_at", None)
                payload.pop("claim_token", None)
                payload.pop("worker_started_at", None)
                payload["status"] = "failed"
                payload["import_error"] = params["error"]
                return _FakeResult([(directory,)])
            return _FakeResult([])

        if sql == COMPENSATE_SCAN_CACHE_CLAIM_SQL.strip():
            if (
                payload.get("status") == "queued"
                and payload.get("claim_token") == params["claim_token"]
                and payload.get("worker_started_at") is None
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


def test_invalid_scan_target_returns_400_without_claim_or_dispatch() -> None:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.imports.dependencies import get_imports_repository
    from miramedia.main import app

    repo = MagicMock()
    repo.claim_scan_cache_row = AsyncMock()

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


def test_started_rows_are_never_selected_for_reclaim() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
                "queued_at": _OLD,
                "worker_started_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 0
        assert state.rows[directory]["status"] == "queued"
        assert state.snapshot_rows() == []

    asyncio.run(_run())


def test_unstarted_stale_row_reclaims() -> None:
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

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        assert state.rows[directory]["status"] == "failed"

    asyncio.run(_run())


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

    async def _run() -> None:
        with _patch_repo_now(datetime.fromisoformat(_FRESH)):
            began = await repo.begin_manual_scan_worker(
                directory, claim_token="token-a", media_type="show"
            )
            assert began.result is ScanWorkerBeginResult.started

            lost = state.execute(
                text(RECLAIM_STALE_QUEUED_IMPORT_SQL),
                {
                    "directory": directory,
                    "expected_claim_token": "token-a",
                    "expected_queued_at": _OLD,
                    "error": "stale snapshot",
                },
            )
            assert lost.first() is None
            assert state.rows[directory]["status"] == "queued"

            claim = await repo.claim_scan_cache_row(directory, media_type="show")
            assert claim.result is ScanClaimResult.not_eligible

    asyncio.run(_run())


def test_legacy_null_token_valid_old_timestamp_reclaims() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "queued_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        assert state.rows[directory]["status"] == "failed"

    asyncio.run(_run())


def test_legacy_missing_timestamp_stamped_then_reclaimed_after_grace() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
            }
        }
    )
    repo = _repo_with_state(state)
    first_seen = datetime.fromisoformat(_FRESH)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(first_seen),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 0
        assert state.rows[directory]["queued_at"] == first_seen.isoformat()

        expired = first_seen + STALE_QUEUED_IMPORT_GRACE + timedelta(minutes=1)
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(expired),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        assert state.rows[directory]["status"] == "failed"

    asyncio.run(_run())


def test_compensation_requires_pre_start_exact_token() -> None:
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

    async def _run() -> None:
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            ok = await repo.compensate_scan_cache_claim(
                directory, claim_token="token-a", error="broker down"
            )
            stale = await repo.compensate_scan_cache_claim(
                directory, claim_token="token-b", error="broker down"
            )

        assert ok is True
        assert stale is False
        assert state.rows[directory]["status"] == "failed"

        state.rows[directory] = {
            "status": "queued",
            "claim_token": "token-a",
            "queued_at": _OLD,
            "worker_started_at": _FRESH,
        }
        repo.db.execute = AsyncMock(side_effect=state.execute)
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            after_begin = await repo.compensate_scan_cache_claim(
                directory, claim_token="token-a", error="broker down"
            )
        assert after_begin is False
        assert state.rows[directory]["status"] == "queued"

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
        assert COMPLETE_MANUAL_SCAN_IMPORT_SQL.strip() in _sql_text(
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


def test_stalled_worker_old_row_reclaims() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
                "queued_at": _OLD,
                "worker_started_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)
    now = datetime.fromisoformat(_OLD) + STALLED_WORKER_GRACE + timedelta(minutes=1)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(now),
        ):
            reclaimed = await repo.reclaim_stalled_worker_imports(
                older_than=STALLED_WORKER_GRACE
            )
        assert reclaimed == 1
        row = state.rows[directory]
        assert row["status"] == "failed"
        assert "process died mid-import" in str(row["import_error"])
        assert "worker_started_at" not in row
        assert "claim_token" not in row
        assert "queued_at" not in row

    asyncio.run(_run())


def test_stalled_worker_fresh_row_untouched() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
                "queued_at": _OLD,
                "worker_started_at": _FRESH,
            }
        }
    )
    repo = _repo_with_state(state)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stalled_worker_imports(
                older_than=STALLED_WORKER_GRACE
            )
        assert reclaimed == 0
        assert state.rows[directory]["status"] == "queued"

    asyncio.run(_run())


def test_stalled_worker_malformed_timestamp_stamped_then_reclaimed() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
                "worker_started_at": "not-a-timestamp",
            }
        }
    )
    repo = _repo_with_state(state)
    first_seen = datetime.fromisoformat(_FRESH)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(first_seen),
        ):
            reclaimed = await repo.reclaim_stalled_worker_imports(
                older_than=STALLED_WORKER_GRACE
            )
        assert reclaimed == 0
        assert state.rows[directory]["worker_started_at"] == first_seen.isoformat()

        expired = first_seen + STALLED_WORKER_GRACE + timedelta(minutes=1)
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(expired),
        ):
            reclaimed = await repo.reclaim_stalled_worker_imports(
                older_than=STALLED_WORKER_GRACE
            )
        assert reclaimed == 1
        assert state.rows[directory]["status"] == "failed"

    asyncio.run(_run())


def test_stalled_worker_reclaim_resets_batch_and_schedules_rebuild() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-a",
                "worker_started_at": _OLD,
            }
        }
    )
    state.batch_total = 3
    repo = _repo_with_state(state)
    now = datetime.fromisoformat(_OLD) + STALLED_WORKER_GRACE + timedelta(minutes=1)
    rebuild = patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild")

    async def _run() -> None:
        with rebuild as rebuild_mock, _patch_repo_now(now):
            reclaimed = await repo.reclaim_stalled_worker_imports(
                older_than=STALLED_WORKER_GRACE
            )
        assert reclaimed == 1
        repo.db.commit.assert_awaited_once()
        rebuild_mock.assert_called_once()
        assert state.batch_total == 0

    asyncio.run(_run())


def test_reclaim_stale_queued_uses_one_batch_update_for_many_rows() -> None:
    state = _StatefulScanCache(
        {
            f"/safe/show-{i}": {
                "status": "queued",
                "claim_token": f"token-{i}",
                "queued_at": _OLD,
            }
            for i in range(3)
        }
    )
    repo = _repo_with_state(state)
    update_calls: list[str] = []

    async def _tracked_execute(stmt: object, params: dict[str, Any] | None = None):
        sql = _sql_text(stmt).strip()
        if sql.startswith("UPDATE scan_result_cache AS q"):
            update_calls.append(sql)
        return state.execute(stmt, params)

    repo.db.execute = AsyncMock(side_effect=_tracked_execute)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 3
        assert len(update_calls) == 1

    asyncio.run(_run())


def test_reclaim_stale_queued_batch_cas_blocks_token_change() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-b",
                "queued_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)
    rows = [(directory, "token-a", _OLD)]
    values_from, params = _build_values_from_clause(
        rows=rows,
        fields=("directory", "expected_claim_token", "expected_queued_at"),
        prefix="reclaim_queued",
    )
    sql = RECLAIM_STALE_QUEUED_IMPORT_BATCH_SQL.format(values_from=values_from)
    params["error"] = "stale snapshot"

    async def _run() -> None:
        result = await repo.db.execute(text(sql), params)
        assert result.first() is None
        assert state.rows[directory]["status"] == "queued"

    asyncio.run(_run())


def test_reclaim_stale_queued_empty_snapshot_executes_no_updates() -> None:
    state = _StatefulScanCache({})
    repo = _repo_with_state(state)

    async def _run() -> None:
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 0
        repo.db.commit.assert_not_awaited()

    asyncio.run(_run())


def test_reclaim_stalled_worker_batch_cas_blocks_snapshot_drift() -> None:
    """Batch stalled-worker reclaim must not win when live row drifted after snapshot."""
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-b",
                "worker_started_at": _FRESH,
            }
        }
    )
    repo = _repo_with_state(state)
    now = datetime.fromisoformat(_OLD) + STALLED_WORKER_GRACE + timedelta(minutes=1)

    async def _execute_with_stale_snapshot(
        stmt: object, params: dict[str, Any] | None = None
    ) -> _FakeResult:
        sql = _sql_text(stmt).strip()
        if sql == SELECT_STARTED_IMPORT_SNAPSHOT_SQL.strip():
            return _FakeResult(
                [
                    _StartedSnapshotRow(
                        directory=directory,
                        claim_token="token-a",
                        worker_started_at=_OLD,
                    )
                ]
            )
        return state.execute(stmt, params)

    repo.db.execute = AsyncMock(side_effect=_execute_with_stale_snapshot)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(now),
        ):
            reclaimed = await repo.reclaim_stalled_worker_imports(
                older_than=STALLED_WORKER_GRACE
            )
        assert reclaimed == 0
        row = state.rows[directory]
        assert row["status"] == "queued"
        assert row["claim_token"] == "token-b"
        assert row["worker_started_at"] == _FRESH

    asyncio.run(_run())


def test_reclaim_stalled_worker_batch_sql_cas_blocks_token_change() -> None:
    directory = "/safe/show"
    state = _StatefulScanCache(
        {
            directory: {
                "status": "queued",
                "claim_token": "token-b",
                "worker_started_at": _OLD,
            }
        }
    )
    repo = _repo_with_state(state)
    rows = [(directory, "token-a", _OLD)]
    values_from, params = _build_values_from_clause(
        rows=rows,
        fields=("directory", "expected_claim_token", "expected_worker_started_at"),
        prefix="reclaim_worker",
    )
    sql = RECLAIM_STALLED_WORKER_IMPORT_BATCH_SQL.format(values_from=values_from)
    params["error"] = "stale snapshot"

    async def _run() -> None:
        result = await repo.db.execute(text(sql), params)
        assert result.first() is None
        assert state.rows[directory]["status"] == "queued"

    asyncio.run(_run())


def test_reclaim_stale_queued_mixed_stamp_and_reclaim_single_commit() -> None:
    stamp_dir = "/safe/stamp-me"
    reclaim_dir = "/safe/reclaim-me"
    state = _StatefulScanCache(
        {
            stamp_dir: {
                "status": "queued",
                "claim_token": "token-stamp",
            },
            reclaim_dir: {
                "status": "queued",
                "claim_token": "token-reclaim",
                "queued_at": _OLD,
            },
        }
    )
    state.batch_total = 2
    repo = _repo_with_state(state)
    stamp_updates: list[str] = []
    reclaim_updates: list[str] = []
    reset_calls = 0
    original_execute = state.execute

    async def _tracked_execute(stmt: object, params: dict[str, Any] | None = None):
        sql = _sql_text(stmt).strip()
        if sql.startswith(_batch_sql_head(STAMP_LEGACY_QUEUED_AT_BATCH_SQL)):
            stamp_updates.append(sql)
        elif "v.expected_queued_at" in sql and "worker_started_at' IS NULL" in sql:
            reclaim_updates.append(sql)
        elif sql == RESET_IMPORT_BATCH_IF_IDLE_SQL.strip():
            nonlocal reset_calls
            reset_calls += 1
        return original_execute(stmt, params)

    repo.db.execute = AsyncMock(side_effect=_tracked_execute)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        assert state.rows[stamp_dir]["queued_at"] == _FRESH
        assert state.rows[stamp_dir]["status"] == "queued"
        assert state.rows[reclaim_dir]["status"] == "failed"
        assert len(stamp_updates) == 1
        assert len(reclaim_updates) == 1
        assert reset_calls == 1
        repo.db.commit.assert_awaited_once()
        # Reset runs only when reclaimed > 0, but stays a no-op while a queued row remains.
        assert state.batch_total == 2

    asyncio.run(_run())


def test_reclaim_stale_queued_chunks_at_five_hundred_rows() -> None:
    row_count = 501
    state = _StatefulScanCache(
        {
            f"/safe/show-{i}": {
                "status": "queued",
                "claim_token": f"token-{i}",
                "queued_at": _OLD,
            }
            for i in range(row_count)
        }
    )
    repo = _repo_with_state(state)
    reclaim_updates: list[str] = []

    async def _tracked_execute(stmt: object, params: dict[str, Any] | None = None):
        sql = _sql_text(stmt).strip()
        if "v.expected_queued_at" in sql and "worker_started_at' IS NULL" in sql:
            reclaim_updates.append(sql)
        return state.execute(stmt, params)

    repo.db.execute = AsyncMock(side_effect=_tracked_execute)

    async def _run() -> None:
        with (
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
            _patch_repo_now(datetime.fromisoformat(_FRESH)),
        ):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == row_count
        assert len(reclaim_updates) == 2
        assert all(row["status"] == "failed" for row in state.rows.values())

    asyncio.run(_run())

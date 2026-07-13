"""Tests for atomic scan-cache claim before resolve dispatch."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.sql.elements import TextClause

from miramedia.imports.repository import (
    BEGIN_MANUAL_SCAN_WORKER_SQL,
    CLAIM_SCAN_CACHE_ROW_SQL,
    COMPENSATE_SCAN_CACHE_CLAIM_SQL,
    FAIL_MANUAL_SCAN_IMPORT_SQL,
    RECLAIM_STALE_QUEUED_IMPORT_SQL,
    RESET_IMPORT_BATCH_IF_IDLE_SQL,
    ImportsRepository,
    ScanClaimOutcome,
    ScanClaimResult,
    ScanWorkerBeginOutcome,
    ScanWorkerBeginResult,
)

PREFIX = "/api/v1/imports"

# Plan 084 owns true concurrent PostgreSQL claim/reclaim races; this lane
# verifies SQL parameters, pre-start reclaim only, and compensation.


class _FakeResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def first(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


def _scan_body(directory: str = "/safe/show") -> dict[str, Any]:
    return {
        "kind": "scan",
        "id": directory,
        "media_type": "show",
        "media_id": str(uuid.uuid4()),
    }


def _repo_with_db(db: MagicMock) -> ImportsRepository:
    return ImportsRepository(db=db)


def _sql_text(call: Any) -> str:
    stmt = call.args[0]
    if isinstance(stmt, TextClause):
        return stmt.text
    return str(stmt)


def _claimed_outcome(token: str | None = None) -> ScanClaimOutcome:
    return ScanClaimOutcome(
        ScanClaimResult.claimed, claim_token=token or str(uuid.uuid4())
    )


def test_claim_sql_requires_status_media_type_and_claim_token() -> None:
    assert "pending" in CLAIM_SCAN_CACHE_ROW_SQL
    assert "failed" in CLAIM_SCAN_CACHE_ROW_SQL
    assert "media_type_hint" in CLAIM_SCAN_CACHE_ROW_SQL
    assert "claim_token" in CLAIM_SCAN_CACHE_ROW_SQL
    assert "worker_started_at" in CLAIM_SCAN_CACHE_ROW_SQL


def test_begin_worker_sql_requires_queued_media_token_and_no_marker() -> None:
    assert "queued" in BEGIN_MANUAL_SCAN_WORKER_SQL
    assert "media_type_hint" in BEGIN_MANUAL_SCAN_WORKER_SQL
    assert "claim_token" in BEGIN_MANUAL_SCAN_WORKER_SQL
    assert "worker_started_at' IS NULL" in BEGIN_MANUAL_SCAN_WORKER_SQL


def test_terminal_sql_requires_worker_started_marker() -> None:
    from miramedia.imports.repository import COMPLETE_MANUAL_SCAN_IMPORT_SQL

    assert "worker_started_at' IS NOT NULL" in COMPLETE_MANUAL_SCAN_IMPORT_SQL
    assert "worker_started_at' IS NOT NULL" in FAIL_MANUAL_SCAN_IMPORT_SQL
    assert "worker_started_at' IS NULL" in RECLAIM_STALE_QUEUED_IMPORT_SQL
    assert "expected_claim_token IS NULL" in RECLAIM_STALE_QUEUED_IMPORT_SQL


def test_claim_success_bumps_batch_in_same_commit() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            None,
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        with patch(
            "miramedia.imports.queue_hooks.schedule_import_queue_rebuild"
        ) as rebuild:
            outcome = await repo.claim_scan_cache_row("/safe/show", media_type="show")

        assert outcome.result is ScanClaimResult.claimed
        assert outcome.claim_token is not None
        assert db.execute.await_count == 2
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
        claim_sql = _sql_text(db.execute.await_args_list[0])
        assert "pending" in claim_sql
        assert "media_type_hint" in claim_sql
        assert "claim_token" in claim_sql
        params = db.execute.await_args_list[0].args[1]
        assert params["directory"] == "/safe/show"
        assert params["media_type"] == "show"
        assert params["claim_token"] == outcome.claim_token
        rebuild.assert_called_once()

    asyncio.run(_run())


def test_second_claim_is_ineligible_without_second_bump() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            None,
            _FakeResult([]),
        ]
    )
    db.scalar = AsyncMock(return_value="/safe/show")
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            first = await repo.claim_scan_cache_row("/safe/show", media_type="show")
            second = await repo.claim_scan_cache_row("/safe/show", media_type="show")

        assert first.result is ScanClaimResult.claimed
        assert second.result is ScanClaimResult.not_eligible
        assert db.execute.await_count == 3
        assert db.commit.await_count == 1

    asyncio.run(_run())


def test_missing_row_returns_not_found() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult([]))
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        outcome = await repo.claim_scan_cache_row("/missing", media_type="show")

        assert outcome.result is ScanClaimResult.not_found
        assert outcome.claim_token is None
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    asyncio.run(_run())


def test_media_type_mismatch_predicate_blocks_claim() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult([]))
    db.scalar = AsyncMock(return_value="/safe/show")
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        outcome = await repo.claim_scan_cache_row("/safe/show", media_type="movie")

        assert outcome.result is ScanClaimResult.not_eligible
        assert db.execute.await_args_list[0].args[1]["media_type"] == "movie"

    asyncio.run(_run())


def test_claim_rolls_back_when_batch_increment_fails() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            RuntimeError("batch increment failed"),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="batch increment failed"):
            await repo.claim_scan_cache_row("/safe/show", media_type="show")

        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    asyncio.run(_run())


def test_compensation_uses_claim_token_and_resets_idle_batch_in_one_commit() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            None,
            None,
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        with patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"):
            ok = await repo.compensate_scan_cache_claim(
                "/safe/show",
                claim_token="token-a",
                error="Failed to queue import task. Press Import to retry.",
            )

        assert ok is True
        assert db.execute.await_count == 3
        compensate_sql = _sql_text(db.execute.await_args_list[0])
        assert "claim_token" in compensate_sql
        assert COMPENSATE_SCAN_CACHE_CLAIM_SQL.strip() in compensate_sql
        assert db.execute.await_args_list[0].args[1]["claim_token"] == "token-a"
        reset_sql = _sql_text(db.execute.await_args_list[2])
        assert RESET_IMPORT_BATCH_IF_IDLE_SQL.strip() in reset_sql
        db.commit.assert_awaited_once()

    asyncio.run(_run())


def test_compensation_with_stale_token_is_noop() -> None:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_FakeResult([])])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        ok = await repo.compensate_scan_cache_claim(
            "/safe/show", claim_token="stale-token", error="broker down"
        )

        assert ok is False
        assert db.execute.await_count == 1
        db.commit.assert_not_awaited()

    asyncio.run(_run())


def test_fail_manual_scan_import_rejects_stale_claim_token() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult([]))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        ok = await repo.fail_manual_scan_import(
            "/safe/show", claim_token="stale-token", error="duplicate delivery"
        )

        assert ok is False
        fail_sql = _sql_text(db.execute.await_args_list[0])
        assert "claim_token" in fail_sql
        assert FAIL_MANUAL_SCAN_IMPORT_SQL.strip() in fail_sql
        db.rollback.assert_awaited_once()

    asyncio.run(_run())


def test_complete_manual_scan_import_rejects_aba_token() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult([]))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        ok = await repo.complete_manual_scan_import(
            "/safe/show",
            claim_token="old-token",
            imported_name="Show",
            imported_media_id=str(uuid.uuid4()),
            imported_media_type="show",
        )

        assert ok is False
        db.rollback.assert_awaited_once()

    asyncio.run(_run())


class _CacheRow:
    def __init__(self, directory: str, payload: dict[str, object]) -> None:
        self.directory = directory
        self.payload = payload


def test_begin_worker_marks_started_in_one_commit() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult([("/safe/show",)]))
    db.commit = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        began = await repo.begin_manual_scan_worker(
            "/safe/show", claim_token="token-a", media_type="show"
        )

        assert began.result is ScanWorkerBeginResult.started
        begin_sql = _sql_text(db.execute.await_args_list[0])
        assert BEGIN_MANUAL_SCAN_WORKER_SQL.strip() in begin_sql
        assert db.execute.await_args_list[0].args[1]["claim_token"] == "token-a"
        db.commit.assert_awaited_once()

    asyncio.run(_run())


def test_second_begin_is_duplicate_without_touching_row() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([("/safe/show",)]),
            _FakeResult([]),
            _FakeResult(
                [
                    _CacheRow(
                        "/safe/show",
                        {
                            "status": "queued",
                            "claim_token": "token-a",
                            "media_type_hint": "show",
                            "worker_started_at": "2026-07-13T00:00:00+00:00",
                        },
                    )
                ]
            ),
        ]
    )
    db.commit = AsyncMock()
    repo = _repo_with_db(db)

    async def _run() -> None:
        first = await repo.begin_manual_scan_worker(
            "/safe/show", claim_token="token-a", media_type="show"
        )
        second = await repo.begin_manual_scan_worker(
            "/safe/show", claim_token="token-a", media_type="show"
        )

        assert first.result is ScanWorkerBeginResult.started
        assert second.result is ScanWorkerBeginResult.duplicate
        assert db.execute.await_count == 3
        assert db.commit.await_count == 1

    asyncio.run(_run())


def test_begin_with_stale_claim_token_returns_stale() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _FakeResult([]),
            _FakeResult(
                [
                    _CacheRow(
                        "/safe/show",
                        {
                            "status": "queued",
                            "claim_token": "new-token",
                            "media_type_hint": "show",
                        },
                    )
                ]
            ),
        ]
    )
    repo = _repo_with_db(db)

    async def _run() -> None:
        began = await repo.begin_manual_scan_worker(
            "/safe/show", claim_token="old-token", media_type="show"
        )

        assert began.result is ScanWorkerBeginResult.stale

    asyncio.run(_run())


def test_duplicate_scan_task_deliveries_invoke_service_once() -> None:
    from contextlib import asynccontextmanager

    from miramedia.imports.schemas import (
        ResolveImportTaskPayload,
        ResolveRequest,
        ResolveResult,
    )
    from miramedia.imports.tasks import resolve_import_task

    body = ResolveRequest.model_validate(_scan_body())
    payload = ResolveImportTaskPayload(body=body, scan_claim_token="token-a")
    service = MagicMock()
    service.resolve_manual_scan = AsyncMock(
        return_value=ResolveResult(ok=True, detail="imported")
    )
    repo = MagicMock()
    repo.begin_manual_scan_worker = AsyncMock(
        side_effect=[
            ScanWorkerBeginOutcome(
                ScanWorkerBeginResult.started,
                worker_started_at="2026-07-13T00:00:00+00:00",
            ),
            ScanWorkerBeginOutcome(ScanWorkerBeginResult.duplicate),
        ]
    )
    repo.fail_manual_scan_import = AsyncMock(return_value=True)

    @asynccontextmanager
    async def _session():
        yield MagicMock()

    async def _run() -> None:
        with (
            patch("miramedia.database.background_session", _session),
            patch(
                "miramedia.torrents.service.TorrentService",
                return_value=MagicMock(),
            ),
            patch(
                "miramedia.indexers.service.IndexerService",
                return_value=MagicMock(),
            ),
            patch(
                "miramedia.notifications.service.NotificationService",
                return_value=MagicMock(),
            ),
            patch(
                "miramedia.shows.service.ShowService",
                return_value=MagicMock(),
            ),
            patch(
                "miramedia.movies.service.MovieService",
                return_value=MagicMock(),
            ),
            patch(
                "miramedia.imports.repository.ImportsRepository",
                return_value=repo,
            ),
            patch(
                "miramedia.imports.service.ImportsService",
                return_value=service,
            ),
            patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
        ):
            dumped = payload.model_dump(mode="json")
            await resolve_import_task(dumped)
            await resolve_import_task(dumped)

        service.resolve_manual_scan.assert_awaited_once()
        repo.fail_manual_scan_import.assert_not_awaited()

    asyncio.run(_run())


@contextmanager
def imports_resolve_client(
    *,
    claim_outcome: ScanClaimOutcome | None = None,
) -> Generator[tuple[TestClient, AsyncMock, AsyncMock]]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.imports.dependencies import get_imports_repository
    from miramedia.main import app

    outcome = claim_outcome or _claimed_outcome()
    repo = MagicMock()
    repo.claim_scan_cache_row = AsyncMock(return_value=outcome)
    repo.compensate_scan_cache_claim = AsyncMock(return_value=True)

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
    with (
        patch(
            "miramedia.imports.router.validate_scan_resolve_request",
            new=AsyncMock(return_value="/safe/show"),
        ),
        patch("miramedia.imports.tasks.resolve_import_task") as task_module,
    ):
        task_module.kiq = task_mock
        try:
            client = TestClient(app, raise_server_exceptions=False)
            yield client, task_mock, repo
        finally:
            app.dependency_overrides.clear()


def test_missing_cache_row_returns_404_without_dispatch() -> None:
    with imports_resolve_client(
        claim_outcome=ScanClaimOutcome(ScanClaimResult.not_found)
    ) as (client, task_mock, _repo):
        response = client.post(f"{PREFIX}/resolve", json=_scan_body())
    assert response.status_code == 404
    task_mock.assert_not_awaited()


def test_ineligible_cache_row_returns_409_without_dispatch() -> None:
    with imports_resolve_client(
        claim_outcome=ScanClaimOutcome(ScanClaimResult.not_eligible)
    ) as (client, task_mock, _repo):
        response = client.post(f"{PREFIX}/resolve", json=_scan_body())
    assert response.status_code == 409
    task_mock.assert_not_awaited()


def test_successful_claim_dispatches_exact_token_payload() -> None:
    token = str(uuid.uuid4())
    with imports_resolve_client(claim_outcome=_claimed_outcome(token)) as (
        client,
        task_mock,
        _repo,
    ):
        response = client.post(f"{PREFIX}/resolve", json=_scan_body())
    assert response.status_code == 202
    payload = task_mock.await_args.args[0]
    assert payload["scan_claim_token"] == token
    assert payload["body"]["id"] == "/safe/show"
    assert "scan_claim_token" not in payload["body"]


def test_broker_failure_compensates_with_same_claim_token() -> None:
    token = str(uuid.uuid4())
    with imports_resolve_client(claim_outcome=_claimed_outcome(token)) as (
        client,
        task_mock,
        repo,
    ):
        task_mock.side_effect = RuntimeError("broker down")
        response = client.post(f"{PREFIX}/resolve", json=_scan_body())
    assert response.status_code == 503
    repo.compensate_scan_cache_claim.assert_awaited_once_with(
        "/safe/show",
        claim_token=token,
        error="Failed to queue import task. Press Import to retry.",
    )

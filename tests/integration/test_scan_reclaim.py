"""Real-DB stale reclaim and terminal CAS (Plans 074/078)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from miramedia.imports.models import ImportBatch, ScanResultCache
from miramedia.imports.repository import (
    RECLAIM_STALE_QUEUED_IMPORT_SQL,
    STALE_QUEUED_IMPORT_GRACE,
    ImportsRepository,
    ScanClaimResult,
    ScanWorkerBeginResult,
)
from tests.integration.builders import seed_import_batch

pytestmark = pytest.mark.integration

_OLD = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
_FRESH = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)


async def _insert_queued_row(
    db,
    *,
    directory: str,
    payload: dict,
) -> None:
    db.add(
        ScanResultCache(
            id=uuid.uuid4(),
            directory=directory,
            payload=payload,
            scanned_at=_FRESH,
        )
    )
    await db.commit()


def test_stale_unstarted_row_reclaims(db, run_async) -> None:
    directory = f"/integration/reclaim/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db)
        await _insert_queued_row(
            db,
            directory=directory,
            payload={
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "token-a",
                "queued_at": _OLD.isoformat(),
            },
        )
        repo = ImportsRepository(db)
        with patch("miramedia.imports.repository._utc_now", return_value=_FRESH):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1
        row = await repo.get_scan_cache_entry(directory)
        assert row is not None
        assert row["status"] == "failed"

    run_async(_run_test())


def test_legacy_null_token_predicate_reclaims(db, run_async) -> None:
    directory = f"/integration/reclaim-null-token/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db)
        await _insert_queued_row(
            db,
            directory=directory,
            payload={
                "status": "queued",
                "media_type_hint": "show",
                "queued_at": _OLD.isoformat(),
            },
        )
        repo = ImportsRepository(db)
        with patch("miramedia.imports.repository._utc_now", return_value=_FRESH):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 1

    run_async(_run_test())


def test_missing_queued_at_stamped_then_grace_before_reclaim(db, run_async) -> None:
    directory = f"/integration/reclaim-stamp/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db)
        await _insert_queued_row(
            db,
            directory=directory,
            payload={
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "token-a",
            },
        )
        repo = ImportsRepository(db)
        with patch("miramedia.imports.repository._utc_now", return_value=_FRESH):
            first = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert first == 0
        row = await repo.get_scan_cache_entry(directory)
        assert row is not None
        assert row["queued_at"] == _FRESH.isoformat()

        expired = _FRESH + STALE_QUEUED_IMPORT_GRACE + timedelta(minutes=1)
        with patch("miramedia.imports.repository._utc_now", return_value=expired):
            second = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert second == 1

    run_async(_run_test())


def test_begin_blocks_stale_reclaim_snapshot(db, run_async) -> None:
    directory = f"/integration/reclaim-begin/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db)
        await _insert_queued_row(
            db,
            directory=directory,
            payload={
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "token-a",
                "queued_at": _OLD.isoformat(),
            },
        )
        repo = ImportsRepository(db)
        with patch("miramedia.imports.repository._utc_now", return_value=_FRESH):
            began = await repo.begin_manual_scan_worker(
                directory, claim_token="token-a", media_type="show"
            )
        assert began.result is ScanWorkerBeginResult.started

        lost = await db.execute(
            text(RECLAIM_STALE_QUEUED_IMPORT_SQL),
            {
                "directory": directory,
                "expected_claim_token": "token-a",
                "expected_queued_at": _OLD.isoformat(),
                "error": "stale snapshot",
            },
        )
        assert lost.first() is None

        claim = await repo.claim_scan_cache_row(directory, media_type="show")
        assert claim.result is ScanClaimResult.not_eligible

    run_async(_run_test())


def test_started_rows_remain_unreclaimed(db, run_async) -> None:
    directory = f"/integration/reclaim-started/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db)
        await _insert_queued_row(
            db,
            directory=directory,
            payload={
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "token-a",
                "queued_at": _OLD.isoformat(),
                "worker_started_at": _OLD.isoformat(),
            },
        )
        repo = ImportsRepository(db)
        with patch("miramedia.imports.repository._utc_now", return_value=_FRESH):
            reclaimed = await repo.reclaim_stale_queued_imports(
                older_than=STALE_QUEUED_IMPORT_GRACE
            )
        assert reclaimed == 0
        row = await repo.get_scan_cache_entry(directory)
        assert row is not None
        assert row["status"] == "queued"

    run_async(_run_test())


def test_terminal_cas_rejects_old_token_and_resets_batch(db, run_async) -> None:
    directory = f"/integration/terminal/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db, total=1)
        await _insert_queued_row(
            db,
            directory=directory,
            payload={
                "status": "queued",
                "media_type_hint": "show",
                "claim_token": "new-token",
                "queued_at": _OLD.isoformat(),
                "worker_started_at": _FRESH.isoformat(),
            },
        )
        repo = ImportsRepository(db)
        rejected = await repo.fail_manual_scan_import(
            directory,
            claim_token="old-token",
            error="stale token",
        )
        assert rejected is False

        accepted = await repo.fail_manual_scan_import(
            directory,
            claim_token="new-token",
            error="done",
        )
        assert accepted is True
        total = await db.scalar(select(ImportBatch.total))
        assert int(total or 0) == 0

    run_async(_run_test())

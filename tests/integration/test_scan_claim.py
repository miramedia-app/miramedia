"""Real-DB atomic scan claim races (Plan 078)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from miramedia.imports.models import ImportBatch
from miramedia.imports.repository import (
    ImportsRepository,
    ScanClaimResult,
    ScanWorkerBeginResult,
)
from tests.integration.builders import seed_import_batch, seed_pending_scan_row

pytestmark = pytest.mark.integration


async def _concurrent_claim(
    make_session,
    *,
    directory: str,
    barrier: asyncio.Barrier,
) -> tuple[object, object]:
    session = make_session()
    repo = ImportsRepository(session)
    try:
        await barrier.wait()
        outcome = await repo.claim_scan_cache_row(directory, media_type="show")
    except Exception:
        await session.close()
        raise
    else:
        return outcome, session


def test_concurrent_claim_exactly_one_winner_and_single_batch_increment(
    make_session,
    run_async,
) -> None:
    directory = f"/integration/claim/{uuid.uuid4()}"

    async def _run_test() -> None:
        setup = make_session()
        await seed_import_batch(setup, total=0)
        await seed_pending_scan_row(setup, directory=directory)
        await setup.close()

        barrier = asyncio.Barrier(2)
        results = await asyncio.gather(
            _concurrent_claim(make_session, directory=directory, barrier=barrier),
            _concurrent_claim(make_session, directory=directory, barrier=barrier),
        )
        for _outcome, session in results:
            await session.close()

        outcomes = [outcome for outcome, _session in results]
        claimed = [
            o
            for o in outcomes
            if o.result is ScanClaimResult.claimed  # type: ignore[attr-defined]
        ]
        not_eligible = [
            o
            for o in outcomes
            if o.result is ScanClaimResult.not_eligible  # type: ignore[attr-defined]
        ]
        assert len(claimed) == 1
        assert len(not_eligible) == 1
        assert claimed[0].claim_token

        verify = make_session()
        total = await verify.scalar(select(ImportBatch.total))
        await verify.close()
        assert int(total or 0) == 1

    run_async(_run_test())


def test_compensation_requires_exact_token_and_pre_start_only(
    db, make_session, run_async
) -> None:
    directory = f"/integration/compensate/{uuid.uuid4()}"

    async def _run_test() -> None:
        await seed_import_batch(db, total=0)
        await seed_pending_scan_row(db, directory=directory)
        repo = ImportsRepository(db)
        claimed = await repo.claim_scan_cache_row(directory, media_type="show")
        assert claimed.result is ScanClaimResult.claimed
        token = claimed.claim_token
        assert token

        ok = await repo.compensate_scan_cache_claim(
            directory, claim_token=token, error="broker down"
        )
        stale = await repo.compensate_scan_cache_claim(
            directory, claim_token=str(uuid.uuid4()), error="broker down"
        )
        assert ok is True
        assert stale is False

        other = make_session()
        other_repo = ImportsRepository(other)
        await seed_import_batch(other, total=0)
        other_directory = f"{directory}-b"
        await seed_pending_scan_row(other, directory=other_directory)
        began = await other_repo.claim_scan_cache_row(
            other_directory, media_type="show"
        )
        assert began.claim_token
        started = await other_repo.begin_manual_scan_worker(
            other_directory,
            claim_token=began.claim_token,  # type: ignore[arg-type]
            media_type="show",
        )
        assert started.result is ScanWorkerBeginResult.started
        blocked = await other_repo.compensate_scan_cache_claim(
            other_directory,
            claim_token=began.claim_token,  # type: ignore[arg-type]
            error="broker down",
        )
        await other.close()
        assert blocked is False

    run_async(_run_test())

"""Real-DB feed lease commit visibility and owner-safe finalize (Plan 439)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from miramedia.feeds.models import FeedSource
from miramedia.feeds.repository import FeedRepository

pytestmark = pytest.mark.integration


async def _seed_source(db) -> FeedSource:
    source = FeedSource(
        id=uuid.uuid4(),
        backend="jackett",
        indexer_key=f"idx-{uuid.uuid4().hex[:8]}",
        protocol="torznab",
        enabled=True,
    )
    db.add(source)
    await db.commit()
    return source


def test_committed_claim_excludes_second_worker(make_session, run_async) -> None:
    async def _run_test() -> None:
        setup = make_session()
        source = await _seed_source(setup)
        await setup.close()

        worker_a = make_session()
        repo_a = FeedRepository(worker_a)
        claim_a = await repo_a.claim_source("worker-a")
        assert claim_a is not None
        assert claim_a.id == source.id
        await worker_a.commit()

        worker_b = make_session()
        repo_b = FeedRepository(worker_b)
        claim_b = await repo_b.claim_source("worker-b")
        assert claim_b is None

        await worker_b.close()
        await worker_a.close()

    run_async(_run_test())


def test_stale_owner_cannot_release_or_finalize_after_reclaim(
    db, make_session, run_async
) -> None:
    async def _run_test() -> None:
        source = await _seed_source(db)
        repo = FeedRepository(db)
        claim_a = await repo.claim_source("worker-a")
        assert claim_a is not None
        await db.commit()

        await db.execute(
            update(FeedSource)
            .where(FeedSource.id == source.id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()

        other = make_session()
        repo_b = FeedRepository(other)
        claim_b = await repo_b.claim_source("worker-b")
        assert claim_b is not None
        assert claim_b.id == source.id
        await other.commit()

        released = await repo.release_lease(source.id, lease_owner="worker-a")
        assert released is False

        finalized = await repo.record_poll_success(
            source.id,
            lease_owner="worker-a",
            watermark_pub_date=None,
            watermark_guid=None,
        )
        assert finalized is False

        row = await other.get(FeedSource, source.id)
        assert row is not None
        assert row.lease_owner == "worker-b"
        await other.close()

    run_async(_run_test())

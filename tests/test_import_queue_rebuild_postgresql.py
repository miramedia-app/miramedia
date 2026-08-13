"""PostgreSQL integration tests for cross-worker import-queue rebuild locking."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from miramedia.imports.models import ImportQueueItem
from miramedia.imports.queue.sync import (
    _acquire_import_queue_rebuild_advisory_lock,
    import_queue_is_empty,
    rebuild_import_queue,
)
from miramedia.imports.repository import ImportsRepository
from miramedia.imports.schemas import TorrentImportItem
from miramedia.imports.service import ImportsService
from miramedia.torrents.schemas import (
    ImportProgress,
    ImportStatusEntry,
    TorrentId,
    TorrentStatus,
)
from tests.pg_disposable import (
    disposable_database_sync_url,
    require_disposable_database_url,
)

pytestmark = pytest.mark.postgresql

_alembic_ready = False


def _async_url(sync_url: str) -> str:
    from sqlalchemy.engine.url import make_url

    url = make_url(sync_url)
    return url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


def _run_alembic_upgrade(sync_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": sync_url}
    proc = subprocess.run(
        ["uv", "run", "--python", "3.13", "alembic", "upgrade", "head"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        msg = (
            "alembic upgrade head failed\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        pytest.fail(msg)


async def _truncate_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename <> 'alembic_version'
                ORDER BY tablename
                """
            )
        )
        tables = [row[0] for row in result]
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            await conn.execute(
                text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
            )


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def run_async(event_loop: asyncio.AbstractEventLoop) -> Callable:
    def _run(coro):
        return event_loop.run_until_complete(coro)

    return _run


@pytest.fixture(scope="session")
def pg_engine(run_async: Callable) -> Iterator[AsyncEngine]:
    global _alembic_ready
    require_disposable_database_url()
    sync_url = disposable_database_sync_url()
    async_url = _async_url(sync_url)
    if not _alembic_ready:
        _run_alembic_upgrade(sync_url)
        _alembic_ready = True
    engine = create_async_engine(async_url, poolclass=NullPool)
    yield engine
    run_async(engine.dispose())


@pytest.fixture(autouse=True)
def clean_database(pg_engine: AsyncEngine, run_async: Callable) -> Iterator[None]:
    run_async(_truncate_tables(pg_engine))
    yield
    run_async(_truncate_tables(pg_engine))


def _make_service(db: AsyncSession) -> ImportsService:
    return ImportsService(
        repository=ImportsRepository(db),
        torrent_service=MagicMock(),
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )


def _sample_torrent_item() -> TorrentImportItem:
    entry = ImportStatusEntry(
        torrent_id=TorrentId(str(uuid.uuid4())),
        torrent_title="Cross-worker test",
        torrent_status=TorrentStatus.finished,
        progress=ImportProgress(total=2, imported=1, pending=1),
        files=[],
    )
    return TorrentImportItem(id=str(entry.torrent_id), entry=entry)


def test_advisory_lock_blocks_second_session_until_commit(
    pg_engine: AsyncEngine, run_async: Callable
) -> None:
    run_async(_advisory_lock_blocks_second_session_until_commit(pg_engine))


async def _advisory_lock_blocks_second_session_until_commit(
    pg_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    worker_a_holds_lock = asyncio.Event()
    worker_b_acquired = asyncio.Event()
    order: list[str] = []
    blocked_for = 0.0

    async def worker_a() -> None:
        async with factory() as db:
            await _acquire_import_queue_rebuild_advisory_lock(db)
            worker_a_holds_lock.set()
            await asyncio.sleep(0.2)
            order.append("a_commit")
            await db.commit()

    async def worker_b() -> None:
        nonlocal blocked_for
        await worker_a_holds_lock.wait()
        started = time.monotonic()
        async with factory() as db:
            await _acquire_import_queue_rebuild_advisory_lock(db)
            worker_b_acquired.set()
            order.append("b_acquired")
            await db.rollback()
        blocked_for = time.monotonic() - started

    await asyncio.gather(worker_a(), worker_b())
    assert worker_b_acquired.is_set()
    assert order == ["a_commit", "b_acquired"]
    assert blocked_for >= 0.15


def test_waiting_worker_skips_rebuild_after_peer_populates_queue(
    pg_engine: AsyncEngine, run_async: Callable
) -> None:
    run_async(_waiting_worker_skips_rebuild_after_peer_populates_queue(pg_engine))


async def _waiting_worker_skips_rebuild_after_peer_populates_queue(
    pg_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    first_collect_started = asyncio.Event()
    release_first_collect = asyncio.Event()
    collect_calls = 0
    sample_item = _sample_torrent_item()

    async def gated_collect(_self: ImportsService) -> list[TorrentImportItem]:
        nonlocal collect_calls
        collect_calls += 1
        if collect_calls == 1:
            first_collect_started.set()
            await release_first_collect.wait()
        return [sample_item]

    async def rebuild_once() -> int:
        async with factory() as db:
            service = _make_service(db)
            with patch.object(ImportsService, "_collect_items", gated_collect):
                return await rebuild_import_queue(db, service, only_if_empty=True)

    leader = asyncio.create_task(rebuild_once())
    await first_collect_started.wait()
    follower = asyncio.create_task(rebuild_once())
    await asyncio.sleep(0.05)
    release_first_collect.set()
    leader_rows, follower_rows = await asyncio.gather(leader, follower)

    assert leader_rows > 0
    assert follower_rows == 0

    async with factory() as db:
        total = int(
            (await db.scalar(select(func.count()).select_from(ImportQueueItem))) or 0
        )
        assert total > 0
        assert await import_queue_is_empty(db) is False


def test_rebuild_failure_leaves_prior_queue_intact(
    pg_engine: AsyncEngine, run_async: Callable
) -> None:
    run_async(_rebuild_failure_leaves_prior_queue_intact(pg_engine))


async def _rebuild_failure_leaves_prior_queue_intact(
    pg_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    sample_item = _sample_torrent_item()

    async with factory() as db:
        service = _make_service(db)
        with patch.object(ImportsService, "_collect_items", return_value=[sample_item]):
            rows = await rebuild_import_queue(db, service)
        assert rows > 0
        before = int(
            (await db.scalar(select(func.count()).select_from(ImportQueueItem))) or 0
        )
        assert before > 0

    async with factory() as db:
        service = _make_service(db)

        async def collect_item(_self: ImportsService) -> list[TorrentImportItem]:
            return [sample_item]

        with patch.object(ImportsService, "_collect_items", collect_item):
            with patch.object(
                db, "commit", AsyncMock(side_effect=RuntimeError("commit failed"))
            ):
                with pytest.raises(RuntimeError, match="commit failed"):
                    await rebuild_import_queue(db, service)

        after = int(
            (await db.scalar(select(func.count()).select_from(ImportQueueItem))) or 0
        )
        assert after == before

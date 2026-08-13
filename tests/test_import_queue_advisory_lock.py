"""DB-free coverage for cross-worker import-queue rebuild advisory locking."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.imports.queue.sync import (
    IMPORT_QUEUE_REBUILD_ADVISORY_LOCK_KEY,
    rebuild_import_queue,
)
from miramedia.imports.service import ImportsService

pytestmark = pytest.mark.usefixtures("reset_queue_globals")


@pytest.fixture
def reset_queue_globals() -> None:
    import miramedia.imports.queue.sync as sync_module

    old_lock = sync_module._rebuild_lock
    sync_module._rebuild_lock = asyncio.Lock()
    yield
    sync_module._rebuild_lock = old_lock


def _make_service() -> ImportsService:
    return ImportsService(
        repository=MagicMock(),
        torrent_service=MagicMock(),
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )


def test_rebuild_acquires_transaction_advisory_lock() -> None:
    async def run() -> None:
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)
        service = _make_service()
        with patch.object(service, "_collect_items", AsyncMock(return_value=[])):
            await rebuild_import_queue(db, service)

        lock_calls = [
            c
            for c in db.execute.call_args_list
            if c.args and "pg_advisory_xact_lock" in str(c.args[0])
        ]
        assert len(lock_calls) == 1
        assert lock_calls[0].args[1]["key"] == IMPORT_QUEUE_REBUILD_ADVISORY_LOCK_KEY
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()

    asyncio.run(run())


def test_only_if_empty_skips_after_post_lock_recheck() -> None:
    async def run() -> None:
        db = AsyncMock()
        service = _make_service()
        with (
            patch(
                "miramedia.imports.queue.sync.import_queue_is_empty",
                AsyncMock(side_effect=[True, False]),
            ) as empty_mock,
            patch.object(
                service, "_collect_items", AsyncMock(return_value=[])
            ) as collect_mock,
        ):
            result = await rebuild_import_queue(db, service, only_if_empty=True)

        assert result == 0
        assert empty_mock.await_count == 2
        collect_mock.assert_awaited_once()
        lock_calls = [
            c
            for c in db.execute.call_args_list
            if c.args and "pg_advisory_xact_lock" in str(c.args[0])
        ]
        assert len(lock_calls) == 2
        assert all(
            c.args[1]["key"] == IMPORT_QUEUE_REBUILD_ADVISORY_LOCK_KEY
            for c in lock_calls
        )
        db.rollback.assert_awaited()
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_only_if_empty_skips_without_collect_when_already_populated() -> None:
    async def run() -> None:
        db = AsyncMock()
        service = _make_service()
        with (
            patch(
                "miramedia.imports.queue.sync.import_queue_is_empty",
                AsyncMock(return_value=False),
            ) as empty_mock,
            patch.object(service, "_collect_items", AsyncMock()) as collect_mock,
        ):
            result = await rebuild_import_queue(db, service, only_if_empty=True)

        assert result == 0
        empty_mock.assert_awaited_once()
        collect_mock.assert_not_awaited()
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_rebuild_rolls_back_on_failure() -> None:
    async def run() -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                None,
                RuntimeError("delete failed"),
            ]
        )
        service = _make_service()
        with patch.object(service, "_collect_items", AsyncMock(return_value=[])):
            with pytest.raises(RuntimeError, match="delete failed"):
                await rebuild_import_queue(db, service)

        db.rollback.assert_awaited()
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_process_local_lock_serializes_same_loop_rebuilds() -> None:
    active = 0
    peak = 0

    async def run() -> None:
        nonlocal active, peak
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)
        service = _make_service()

        async def slow_collect() -> list:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return []

        with patch.object(service, "_collect_items", slow_collect):
            await asyncio.gather(
                rebuild_import_queue(db, service),
                rebuild_import_queue(db, service),
            )

        assert peak == 1

    asyncio.run(run())

"""Incremental import-queue maintenance: bounded work per reference."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import miramedia.imports.queue_hooks as queue_hooks
from miramedia.imports.queue.projector import project_queue_rows
from miramedia.imports.queue.refresh import (
    remove_import_queue_reference,
    sync_import_queue_item,
    sync_scan_import_queue,
)
from miramedia.imports.schemas import ScanImportItem, ScanResult, TorrentImportItem
from miramedia.imports.service import ImportsService
from miramedia.torrents.schemas import (
    ImportProgress,
    ImportStatusEntry,
    TorrentId,
    TorrentStatus,
)

pytestmark = pytest.mark.usefixtures("reset_incremental_hooks")


@pytest.fixture
def reset_incremental_hooks() -> None:
    old_debounce = queue_hooks._incremental_debounce
    old_waiting = queue_hooks._incremental_waiting
    old_rerun = queue_hooks._incremental_rerun
    old_refs = dict(queue_hooks._pending_refs)
    old_completions = dict(queue_hooks._pending_completions)
    queue_hooks._incremental_debounce = None
    queue_hooks._incremental_waiting = False
    queue_hooks._incremental_rerun = False
    queue_hooks._pending_refs.clear()
    queue_hooks._pending_completions.clear()
    yield
    if (
        queue_hooks._incremental_debounce is not None
        and not queue_hooks._incremental_debounce.done()
    ):
        queue_hooks._incremental_debounce.cancel()
    queue_hooks._incremental_debounce = old_debounce
    queue_hooks._incremental_waiting = old_waiting
    queue_hooks._incremental_rerun = old_rerun
    queue_hooks._pending_refs.clear()
    queue_hooks._pending_refs.update(old_refs)
    queue_hooks._pending_completions.clear()
    queue_hooks._pending_completions.update(old_completions)


def _make_service() -> ImportsService:
    return ImportsService(
        repository=MagicMock(),
        torrent_service=MagicMock(),
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )


def _torrent_item() -> TorrentImportItem:
    entry = ImportStatusEntry(
        torrent_id=TorrentId(str(uuid.uuid4())),
        torrent_title="Incremental",
        torrent_status=TorrentStatus.finished,
        progress=ImportProgress(total=2, ambiguous=1, imported=1),
        files=[],
    )
    return TorrentImportItem(id=str(entry.torrent_id), entry=entry)


def _insert_tabs(db: AsyncMock) -> set[str]:
    tabs: set[str] = set()
    for call in db.execute.call_args_list:
        if len(call.args) >= 2 and isinstance(call.args[1], list):
            for row in call.args[1]:
                tabs.add(row["tab"])
    return tabs


def test_project_queue_rows_emits_only_matching_tabs() -> None:
    service = _make_service()
    item = _torrent_item()
    rows = project_queue_rows(service, item)
    assert {r["tab"] for r in rows.values()} == {"review", "all"}


def test_sync_import_queue_item_bounded_sql() -> None:
    async def run() -> None:
        db = AsyncMock()
        service = _make_service()
        item = _torrent_item()
        with patch.object(
            ImportsService, "_collect_items", AsyncMock()
        ) as collect_mock:
            rows = await sync_import_queue_item(db, service, item)
        collect_mock.assert_not_awaited()
        assert rows == 2
        lock_calls = [
            c
            for c in db.execute.call_args_list
            if c.args and "pg_advisory_xact_lock" in str(c.args[0])
        ]
        assert len(lock_calls) == 1
        assert db.execute.await_count == 3
        db.commit.assert_awaited_once()

    asyncio.run(run())


def test_sync_scan_removes_stale_tabs_when_status_changes() -> None:
    async def run() -> None:
        db = AsyncMock()
        service = _make_service()
        pending = ScanImportItem(
            id="/movies/Foo",
            result=ScanResult(
                directory="/movies/Foo",
                detected_name="Foo",
                library_name="movies",
                status="pending",
            ),
        )
        imported = ScanImportItem(
            id="/movies/Foo",
            result=ScanResult(
                directory="/movies/Foo",
                detected_name="Foo",
                library_name="movies",
                status="imported",
            ),
        )
        service.build_scan_import_item = AsyncMock(side_effect=[pending, imported])
        await sync_scan_import_queue(db, service, "/movies/Foo")
        assert _insert_tabs(db) == {"review", "all"}
        db.reset_mock()
        await sync_scan_import_queue(db, service, "/movies/Foo")
        assert _insert_tabs(db) == {"done", "all"}

    asyncio.run(run())


def test_remove_reference_does_not_collect_library() -> None:
    async def run() -> None:
        db = AsyncMock()
        with patch.object(
            ImportsService, "_collect_items", AsyncMock()
        ) as collect_mock:
            await remove_import_queue_reference(db, kind="scan", ref_id="/movies/Gone")
        collect_mock.assert_not_awaited()
        db.commit.assert_awaited_once()

    asyncio.run(run())


def test_burst_reference_schedule_coalesces() -> None:
    flush_count = 0

    async def counting_flush() -> None:
        nonlocal flush_count
        flush_count += 1

    async def run() -> None:
        with (
            patch.object(queue_hooks, "_flush_incremental_queue", counting_flush),
            patch.object(queue_hooks, "_INCREMENTAL_DEBOUNCE_S", 0.05),
        ):
            queue_hooks.schedule_scan_queue_sync("/a")
            queue_hooks.schedule_scan_queue_sync("/a")
            queue_hooks.schedule_scan_queue_sync("/b")
            task = queue_hooks._incremental_debounce
            assert task is not None
            await task
        assert flush_count == 1

    asyncio.run(run())


def test_full_rebuild_repairs_missing_row() -> None:
    async def run() -> None:
        from miramedia.imports.queue.sync import rebuild_import_queue

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=0)
        service = _make_service()
        item = _torrent_item()
        with patch.object(
            service, "_collect_items", AsyncMock(return_value=[item])
        ) as collect_mock:
            rows = await rebuild_import_queue(db, service)
        collect_mock.assert_awaited_once()
        assert rows == 2

    asyncio.run(run())

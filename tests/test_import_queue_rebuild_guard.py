"""Serialize cold-start import-queue rebuilds across concurrent dashboard polls."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import miramedia.imports.queue_hooks as queue_hooks
import miramedia.imports.service as service_module
from miramedia.imports.service import ImportsService

pytestmark = pytest.mark.usefixtures("reset_queue_globals")


@pytest.fixture
def reset_queue_globals() -> None:
    old_built_at = service_module._queue_built_at
    service_module._queue_built_at = None
    service_module._QUEUE_BUILD_LOCK = None
    yield
    service_module._queue_built_at = old_built_at
    service_module._QUEUE_BUILD_LOCK = None


@pytest.fixture
def reset_queue_hooks_globals() -> None:
    old_debounce = queue_hooks._rebuild_debounce
    old_waiting = queue_hooks._rebuild_waiting
    old_rerun = queue_hooks._rerun_requested
    queue_hooks._rebuild_debounce = None
    queue_hooks._rebuild_waiting = False
    queue_hooks._rerun_requested = False
    yield
    if (
        queue_hooks._rebuild_debounce is not None
        and not queue_hooks._rebuild_debounce.done()
    ):
        queue_hooks._rebuild_debounce.cancel()
    queue_hooks._rebuild_debounce = old_debounce
    queue_hooks._rebuild_waiting = old_waiting
    queue_hooks._rerun_requested = old_rerun


def _make_service() -> ImportsService:
    return ImportsService(
        repository=MagicMock(),
        torrent_service=MagicMock(),
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )


def test_concurrent_queue_populate_rebuilds_once() -> None:
    rebuild_count = 0

    async def slow_rebuild(_db: object, _svc: ImportsService) -> None:
        nonlocal rebuild_count
        rebuild_count += 1
        await asyncio.sleep(0.15)

    async def always_empty(_db: object) -> bool:
        return True

    async def run() -> None:
        svc = _make_service()
        db = MagicMock()
        with (
            patch(
                "miramedia.imports.queue.sync.import_queue_is_empty",
                always_empty,
            ),
            patch(
                "miramedia.imports.queue.sync.rebuild_import_queue",
                slow_rebuild,
            ),
        ):
            await asyncio.gather(
                svc._ensure_queue_populated(db),
                svc._ensure_queue_populated(db),
            )
        assert rebuild_count == 1

    asyncio.run(run())


@pytest.mark.usefixtures("reset_queue_hooks_globals")
def test_mid_rebuild_schedule_does_not_cancel() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    first_completed = asyncio.Event()
    completions: list[str] = []

    async def slow_rebuild() -> None:
        started.set()
        await release.wait()
        completions.append("done")
        if len(completions) == 1:
            first_completed.set()

    async def run() -> None:
        with (
            patch.object(queue_hooks, "_rebuild_queue", slow_rebuild),
            patch.object(queue_hooks, "_REBUILD_DEBOUNCE_S", 0.01),
        ):
            queue_hooks.schedule_import_queue_rebuild()
            await started.wait()
            queue_hooks.schedule_import_queue_rebuild()
            release.set()
            await first_completed.wait()
        assert completions == ["done"]

    asyncio.run(run())


@pytest.mark.usefixtures("reset_queue_hooks_globals")
def test_mid_rebuild_schedule_triggers_follow_up_rebuild() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completions: list[str] = []

    async def slow_rebuild() -> None:
        started.set()
        await release.wait()
        completions.append("done")

    async def run() -> None:
        with (
            patch.object(queue_hooks, "_rebuild_queue", slow_rebuild),
            patch.object(queue_hooks, "_REBUILD_DEBOUNCE_S", 0.01),
        ):
            queue_hooks.schedule_import_queue_rebuild()
            await started.wait()
            queue_hooks.schedule_import_queue_rebuild()
            release.set()
            task = queue_hooks._rebuild_debounce
            assert task is not None
            await task
        assert completions == ["done", "done"]

    asyncio.run(run())


@pytest.mark.usefixtures("reset_queue_hooks_globals")
def test_burst_schedule_during_sleep_coalesces_to_one_rebuild() -> None:
    rebuild_count = 0

    async def counting_rebuild() -> None:
        nonlocal rebuild_count
        rebuild_count += 1

    async def run() -> None:
        with (
            patch.object(queue_hooks, "_rebuild_queue", counting_rebuild),
            patch.object(queue_hooks, "_REBUILD_DEBOUNCE_S", 0.05),
        ):
            queue_hooks.schedule_import_queue_rebuild()
            await asyncio.sleep(0.01)
            queue_hooks.schedule_import_queue_rebuild()
            queue_hooks.schedule_import_queue_rebuild()
            task = queue_hooks._rebuild_debounce
            assert task is not None
            await task
        assert rebuild_count == 1

    asyncio.run(run())


def test_queue_ttl_fast_path_skips_rebuild() -> None:
    rebuild_count = 0

    async def counting_rebuild(_db: object, _svc: ImportsService) -> None:
        nonlocal rebuild_count
        rebuild_count += 1

    async def always_empty(_db: object) -> bool:
        return True

    async def run() -> None:
        svc = _make_service()
        db = MagicMock()
        with (
            patch(
                "miramedia.imports.queue.sync.import_queue_is_empty",
                always_empty,
            ),
            patch(
                "miramedia.imports.queue.sync.rebuild_import_queue",
                counting_rebuild,
            ),
        ):
            await svc._ensure_queue_populated(db)
            await svc._ensure_queue_populated(db)
        assert rebuild_count == 1

    asyncio.run(run())

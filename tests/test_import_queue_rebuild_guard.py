"""Serialize cold-start import-queue rebuilds across concurrent dashboard polls."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

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

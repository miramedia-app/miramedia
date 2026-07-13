"""Non-blocking scan lock: overlapping triggers must not run the body twice."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import miramedia.imports.tasks as tasks

pytestmark = pytest.mark.usefixtures("reset_scan_lock")


@pytest.fixture
def reset_scan_lock() -> None:
    tasks._SCAN_LOCK = None
    yield
    tasks._SCAN_LOCK = None


def _scan_patches(
    sleep_seconds: float,
    body_runs: list[int],
    *,
    set_scan_run: AsyncMock | None = None,
) -> list:
    async def slow_scan_libraries(_ignored_paths: set[str]) -> MagicMock:
        body_runs.append(1)
        await asyncio.sleep(sleep_seconds)
        return MagicMock(items=[])

    @asynccontextmanager
    async def mock_bg_session():
        yield MagicMock()

    @asynccontextmanager
    async def mock_service():
        service = MagicMock()
        service.get_all_show_ids = AsyncMock(return_value=[])
        service.get_all_movie_ids = AsyncMock(return_value=[])
        yield service

    mock_repo = MagicMock()
    mock_repo.set_scan_run = set_scan_run or AsyncMock()
    mock_repo.list_ignored_paths = AsyncMock(return_value=[])
    mock_repo.list_terminal_scan_cache = AsyncMock(return_value=[])
    mock_repo.replace_scan_cache = AsyncMock()

    mock_config = MagicMock()
    mock_config.imports.auto_import_on_scan = False

    return [
        patch("miramedia.config.MiraMediaConfig", return_value=mock_config),
        patch("miramedia.database.background_session", mock_bg_session),
        patch("miramedia.database.bg_show_service", mock_service),
        patch("miramedia.database.bg_movie_service", mock_service),
        patch("miramedia.imports.repository.ImportsRepository", return_value=mock_repo),
        patch("miramedia.imports.scan.scan_libraries", slow_scan_libraries),
    ]


def test_concurrent_scan_triggers_run_body_once() -> None:
    body_runs: list[int] = []
    patches = _scan_patches(0.15, body_runs)

    async def run() -> None:
        for patcher in patches:
            patcher.start()
        try:
            await asyncio.gather(
                tasks._scan_and_cache(),
                tasks._scan_and_cache(),
            )
            assert len(body_runs) == 1

            body_runs.clear()
            await tasks._scan_and_cache()
            assert len(body_runs) == 1
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    asyncio.run(run())


def test_scan_early_return_does_not_flip_scan_run() -> None:
    body_runs: list[int] = []
    set_scan_run_calls: list[object] = []

    async def track_set_scan_run(*, state: object, **_kwargs: object) -> None:
        set_scan_run_calls.append(state)

    patches = _scan_patches(
        0.15,
        body_runs,
        set_scan_run=AsyncMock(side_effect=track_set_scan_run),
    )

    async def run() -> None:
        for patcher in patches:
            patcher.start()
        try:
            first = asyncio.create_task(tasks._scan_and_cache())
            await asyncio.sleep(0.02)
            await tasks._scan_and_cache()
            await first
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        assert len(body_runs) == 1
        assert set_scan_run_calls.count(tasks.ScanRunState.running) == 1

    asyncio.run(run())

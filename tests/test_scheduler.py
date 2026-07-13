"""Minimal characterization tests for scheduler pure surfaces."""

from __future__ import annotations

import asyncio

import pytest

import miramedia.scheduler as scheduler


def test_import_sweep_lock_serializes_overlapping_acquisition() -> None:
    async def run() -> None:
        lock = scheduler._import_sweep_lock("test-key")
        assert lock.locked() is False
        async with lock:
            assert lock.locked() is True
            # Non-blocking check: second acquire would block; locked() is True.
            assert scheduler._import_sweep_lock("test-key") is lock

    asyncio.run(run())


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (1, "0 */1 * * *"),
        (6, "0 */6 * * *"),
        (0, "0 */1 * * *"),
    ],
)
def test_interval_cron(hours: int, expected: str) -> None:
    assert scheduler._interval_cron(hours) == expected


def test_get_dynamic_schedule_targets_includes_import_sweeps() -> None:
    targets = scheduler.get_dynamic_schedule_targets()
    assert "miramedia.scheduler:import_all_show_torrents_task" in targets
    assert "miramedia.scheduler:import_all_movie_torrents_task" in targets
    assert "miramedia.scheduler:auto_download_missing_episodes_task" in targets

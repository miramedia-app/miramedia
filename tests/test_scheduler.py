"""Minimal characterization tests for scheduler pure surfaces."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import miramedia.scheduler as scheduler
from miramedia.scheduler_tasks import media as media_tasks
from miramedia.scheduler_tasks.locks import import_sweep_lock


def test_import_sweep_lock_serializes_overlapping_acquisition() -> None:
    async def run() -> None:
        lock = import_sweep_lock("test-key")
        assert lock.locked() is False
        async with lock:
            assert lock.locked() is True
            # Non-blocking check: second acquire would block; locked() is True.
            assert import_sweep_lock("test-key") is lock

    asyncio.run(run())


def test_integrity_sweep_skips_when_lock_held() -> None:
    async def run() -> None:
        lock = import_sweep_lock("integrity")
        async with lock:
            config = MagicMock()
            config.misc.integrity_check_enabled = True

            mock_background_session = MagicMock(
                side_effect=lambda: (_ for _ in ()).throw(
                    AssertionError("integrity sweep should skip while lock is held")
                )
            )

            with (
                patch(
                    "miramedia.scheduler_tasks.integrity.MiraMediaConfig",
                    return_value=config,
                ),
                patch(
                    "miramedia.scheduler_tasks.integrity.background_session",
                    mock_background_session,
                ),
            ):
                await scheduler.verify_imported_files_task()

            mock_background_session.assert_not_called()

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


def test_enqueue_import_all_dispatch_populated_after_scheduler_import() -> None:
    from miramedia.scheduler_tasks import dispatch as dispatch_tasks

    assert dispatch_tasks.enqueue_import_all is not None


def test_notify_add_failure_uses_notification_singleton() -> None:
    calls: list[tuple[str, str]] = []

    with patch(
        "miramedia.notifications.manager.notification_manager.send_notification",
        side_effect=lambda title, message: calls.append((title, message)),
    ):
        media_tasks.notify_add_failure("movie", "tt123", ValueError("boom"))

    assert calls == [("Could not add movie", "tt123: boom")]


def test_notify_update_available_uses_notification_singleton() -> None:
    calls: list[tuple[str, str]] = []
    info = MagicMock(
        latest_version="2.0.0",
        current_version="1.0.0",
        release_url="https://example.com/release",
    )

    with patch(
        "miramedia.notifications.manager.notification_manager.send_notification",
        side_effect=lambda title, message: calls.append((title, message)),
    ):
        media_tasks.notify_update_available(info)

    assert len(calls) == 1
    assert calls[0][0] == "MiraMedia update available"
    assert "2.0.0" in calls[0][1]
    assert "1.0.0" in calls[0][1]

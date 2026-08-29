"""Scheduler registration for Jellyfin viewing-state dry-run."""

import miramedia.scheduler as scheduler


def test_jellyfin_viewing_state_dry_run_task_registered() -> None:
    tasks = scheduler.background_broker.get_all_tasks()
    assert "miramedia.scheduler:jellyfin_viewing_state_dry_run_task" in tasks


def test_viewing_sync_disabled_by_default() -> None:
    from miramedia.config import MiraMediaConfig

    assert MiraMediaConfig().viewing_sync.enabled is False

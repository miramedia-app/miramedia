"""Regression: observe feed task registered; auto-download sweeps remain."""

import miramedia.scheduler as scheduler


def test_observe_release_feeds_task_registered():
    tasks = scheduler.background_broker.get_all_tasks()
    assert "miramedia.scheduler:observe_release_feeds_task" in tasks


def test_auto_download_tasks_still_registered():
    tasks = scheduler.background_broker.get_all_tasks()
    assert "miramedia.scheduler:auto_download_missing_movies_task" in tasks
    assert "miramedia.scheduler:auto_download_missing_episodes_task" in tasks


def test_observe_feeds_disabled_by_default():
    from miramedia.config import MiraMediaConfig

    assert MiraMediaConfig().misc.release_feeds_enabled is False

"""Regression snapshot for scheduler task registration (plan 381)."""

from __future__ import annotations

import importlib

import miramedia.scheduler as scheduler


def _registered_task_snapshot() -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for broker_name, broker in (
        ("interactive", scheduler.interactive_broker),
        ("background", scheduler.background_broker),
    ):
        for task_name, task in broker.get_all_tasks().items():
            if not task_name.startswith("miramedia.scheduler:"):
                continue
            snapshot[task_name] = {
                "broker": broker_name,
                "labels": dict(task.labels),
            }
    return snapshot


def _startup_schedule_snapshot() -> dict[str, list[dict[str, str]]]:
    return {
        task_name: [dict(entry) for entry in entries]
        for task_name, entries in scheduler._STARTUP_SCHEDULES.items()
    }


EXPECTED_TASK_NAMES = frozenset(
    {
        "miramedia.scheduler:add_movie_task",
        "miramedia.scheduler:add_show_task",
        "miramedia.scheduler:auto_download_missing_episodes_task",
        "miramedia.scheduler:auto_download_missing_movies_task",
        "miramedia.scheduler:check_for_updates_task",
        "miramedia.scheduler:cleanup_expired_manual_parse_tokens_task",
        "miramedia.scheduler:cleanup_hls_cache_task",
        "miramedia.scheduler:cleanup_old_logs_task",
        "miramedia.scheduler:cleanup_old_notifications_task",
        "miramedia.scheduler:cleanup_poster_variants_task",
        "miramedia.scheduler:detect_finished_downloads_task",
        "miramedia.scheduler:fulfill_approved_requests_task",
        "miramedia.scheduler:import_all_movie_torrents_task",
        "miramedia.scheduler:import_all_show_torrents_task",
        "miramedia.scheduler:purge_old_indexer_query_results_task",
        "miramedia.scheduler:purge_old_taskiq_messages_task",
        "miramedia.scheduler:reclaim_stale_queued_imports_task",
        "miramedia.scheduler:save_native_resume_data_task",
        "miramedia.scheduler:scan_missing_subtitles_task",
        "miramedia.scheduler:scheduled_library_scan_task",
        "miramedia.scheduler:update_all_movies_metadata_task",
        "miramedia.scheduler:update_all_shows_metadata_task",
        "miramedia.scheduler:verify_imported_files_task",
    }
)

EXPECTED_STARTUP_SCHEDULE_KEYS = frozenset(EXPECTED_TASK_NAMES) - {
    "miramedia.scheduler:add_movie_task",
    "miramedia.scheduler:add_show_task",
}


def test_registered_task_names_and_labels_snapshot() -> None:
    snapshot = _registered_task_snapshot()
    assert frozenset(snapshot) == EXPECTED_TASK_NAMES
    for task_name in EXPECTED_TASK_NAMES:
        labels = snapshot[task_name]["labels"]
        assert "labels" in labels
        priority = labels["labels"]["priority"]
        if task_name.endswith(("add_movie_task", "add_show_task")):
            assert priority == "interactive"
            assert snapshot[task_name]["broker"] == "interactive"
        else:
            assert priority == "background"
            assert snapshot[task_name]["broker"] == "background"


def test_startup_schedules_snapshot_keys() -> None:
    schedules = _startup_schedule_snapshot()
    assert frozenset(schedules) == EXPECTED_STARTUP_SCHEDULE_KEYS
    for entries in schedules.values():
        assert len(entries) == 1
        assert "cron" in entries[0]
        assert entries[0]["cron"]


def test_startup_schedules_match_task_labels() -> None:
    tasks = _registered_task_snapshot()
    schedules = _startup_schedule_snapshot()
    for task_name in schedules:
        assert task_name in tasks


def test_scheduler_entrypoint_main_exists(monkeypatch) -> None:
    monkeypatch.setattr("miramedia.logging.setup_logging", lambda: None)
    module = importlib.import_module("miramedia.scheduler_entrypoint")
    assert callable(module.main)

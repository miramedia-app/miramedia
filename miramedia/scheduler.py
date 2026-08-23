import logging

import taskiq_fastapi
from taskiq import TaskiqScheduler
from taskiq.cli.scheduler.run import SchedulerLoop
from taskiq_postgresql import PostgresqlBroker
from taskiq_postgresql.scheduler_source import PostgresqlSchedulerSource

from miramedia.config import MiraMediaConfig
from miramedia.scheduler_tasks import dispatch as dispatch_tasks
from miramedia.scheduler_tasks import integrity as integrity_tasks
from miramedia.scheduler_tasks import maintenance as maintenance_tasks
from miramedia.scheduler_tasks import media as media_tasks
from miramedia.scheduler_tasks.maintenance import (  # noqa: F401
    POSTER_VARIANT_WIDTHS,
    evict_poster_variants,
)


def _build_db_connection_string_for_taskiq() -> str:
    from urllib.parse import quote

    from miramedia.database import render_db_url

    db_config = MiraMediaConfig().database
    base = render_db_url(
        db_config.user,
        db_config.password,
        db_config.host,
        db_config.port,
        db_config.dbname,
        driver="psycopg_plain",
    )
    libpq_options = quote("-c idle_in_transaction_session_timeout=0", safe="")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options={libpq_options}"


interactive_broker = PostgresqlBroker(
    dsn=_build_db_connection_string_for_taskiq,
    driver="psycopg",
    channel_name="taskiq_interactive",
    table_name="taskiq_messages_interactive",
    run_migrations=True,
)
background_broker = PostgresqlBroker(
    dsn=_build_db_connection_string_for_taskiq,
    driver="psycopg",
    channel_name="taskiq_background",
    table_name="taskiq_messages_background",
    run_migrations=True,
)

broker = background_broker

taskiq_fastapi.init(interactive_broker, "miramedia.main:app")
taskiq_fastapi.init(background_broker, "miramedia.main:app")

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Task / service lifetime
# --------------------------------------------------------------------------
# Tasks below DELIBERATELY do not declare ``TaskiqDepends(get_<svc>)`` —
# that resolution chain ends at ``DbSessionDependency`` -> ``get_session``,
# an async generator that taskiq opens once at task start and tears down at
# task end. The bound asyncpg connection therefore sits ``idle in transaction``
# from the first SELECT until the task body returns — minutes when these
# tasks fan out to subliminal HTTP / libtorrent RPC / chromium fetches. The
# request pool drains, and UI requests stall on ``pool_timeout``.
#
# Instead, task bodies open a fresh ``bg_<svc>_service()`` against the
# dedicated background pool. The short-lived helper closes its session as
# soon as the inner ``await`` block exits, releasing the connection back to
# the pool before any slow external I/O.
@background_broker.task(labels={"priority": "background"})
async def import_all_movie_torrents_task() -> None:
    await media_tasks.import_all_movie_torrents()


@background_broker.task(labels={"priority": "background"})
async def import_all_show_torrents_task() -> None:
    await media_tasks.import_all_show_torrents()


async def _enqueue_import_all() -> None:
    await import_all_movie_torrents_task.kiq()
    await import_all_show_torrents_task.kiq()


dispatch_tasks.enqueue_import_all = _enqueue_import_all


@background_broker.task(labels={"priority": "background"})
async def detect_finished_downloads_task() -> None:
    await media_tasks.detect_finished_downloads()


@background_broker.task(labels={"priority": "background"})
async def update_all_movies_metadata_task() -> None:
    await media_tasks.update_all_movies_metadata()


@background_broker.task(labels={"priority": "background"})
async def update_all_shows_metadata_task() -> None:
    await media_tasks.update_all_shows_metadata()


@background_broker.task(labels={"priority": "background"})
async def auto_download_missing_episodes_task() -> None:
    await media_tasks.auto_download_missing_episodes()


@background_broker.task(labels={"priority": "background"})
async def auto_download_missing_movies_task() -> None:
    await media_tasks.auto_download_missing_movies()


@interactive_broker.task(labels={"priority": "interactive"})
async def add_show_task(
    external_id: str,
    metadata_provider_name: str,
    language: str | None = None,
) -> None:
    await media_tasks.add_show(external_id, metadata_provider_name, language)


@interactive_broker.task(labels={"priority": "interactive"})
async def add_movie_task(
    external_id: str,
    metadata_provider_name: str,
    language: str | None = None,
) -> None:
    await media_tasks.add_movie(external_id, metadata_provider_name, language)


# Maps each task to its cron schedule so PostgresqlSchedulerSource can seed
# the taskiq_schedulers table on first startup.
_STARTUP_SCHEDULES: dict[str, list[dict[str, str]]] = {
    import_all_movie_torrents_task.task_name: [{"cron": "*/5 * * * *"}],
    import_all_show_torrents_task.task_name: [{"cron": "*/5 * * * *"}],
    detect_finished_downloads_task.task_name: [{"cron": "* * * * *"}],
    update_all_movies_metadata_task.task_name: [{"cron": "0 0 * * 1"}],
    update_all_shows_metadata_task.task_name: [{"cron": "0 0 * * 1"}],
    auto_download_missing_episodes_task.task_name: [
        {"cron": f"0 */{MiraMediaConfig().misc.auto_download_interval_hours} * * *"}
    ],
    auto_download_missing_movies_task.task_name: [
        {"cron": f"0 */{MiraMediaConfig().misc.auto_download_interval_hours} * * *"}
    ],
}


@background_broker.task(labels={"priority": "background"})
async def reclaim_stale_queued_imports_task() -> None:
    await maintenance_tasks.reclaim_stale_queued_imports()


_STARTUP_SCHEDULES[reclaim_stale_queued_imports_task.task_name] = [
    {"cron": "*/10 * * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def cleanup_old_logs_task() -> None:
    await maintenance_tasks.cleanup_old_logs()


_STARTUP_SCHEDULES[cleanup_old_logs_task.task_name] = [{"cron": "0 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def cleanup_poster_variants_task() -> None:
    await maintenance_tasks.cleanup_poster_variants()


_STARTUP_SCHEDULES[cleanup_poster_variants_task.task_name] = [{"cron": "30 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def cleanup_hls_cache_task() -> None:
    await maintenance_tasks.cleanup_hls_cache()


_STARTUP_SCHEDULES[cleanup_hls_cache_task.task_name] = [{"cron": "30 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def purge_old_indexer_query_results_task() -> None:
    await maintenance_tasks.purge_old_indexer_query_results()


_STARTUP_SCHEDULES[purge_old_indexer_query_results_task.task_name] = [
    {"cron": "15 3 * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def cleanup_old_notifications_task() -> None:
    await maintenance_tasks.cleanup_old_notifications()


_STARTUP_SCHEDULES[cleanup_old_notifications_task.task_name] = [{"cron": "0 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def cleanup_expired_manual_parse_tokens_task() -> None:
    await maintenance_tasks.cleanup_expired_manual_parse_tokens()


_STARTUP_SCHEDULES[cleanup_expired_manual_parse_tokens_task.task_name] = [
    {"cron": "*/15 * * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def save_native_resume_data_task() -> None:
    await maintenance_tasks.save_native_resume_data()


_STARTUP_SCHEDULES[save_native_resume_data_task.task_name] = [{"cron": "*/5 * * * *"}]


@background_broker.task(labels={"priority": "background"})
async def purge_old_taskiq_messages_task() -> None:
    table_names = {b.table_name for b in (interactive_broker, background_broker)}
    await maintenance_tasks.purge_old_taskiq_messages(taskiq_table_names=table_names)


_STARTUP_SCHEDULES[purge_old_taskiq_messages_task.task_name] = [{"cron": "30 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def verify_imported_files_task() -> None:
    await integrity_tasks.verify_imported_files()


_integrity_interval_hours = max(
    1, MiraMediaConfig().misc.integrity_check_interval_hours
)
_STARTUP_SCHEDULES[verify_imported_files_task.task_name] = [
    {"cron": f"0 */{_integrity_interval_hours} * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def check_for_updates_task() -> None:
    await media_tasks.check_for_updates()


_update_interval = MiraMediaConfig().updates.check_interval_hours
_STARTUP_SCHEDULES[check_for_updates_task.task_name] = [
    {"cron": f"0 */{max(1, _update_interval)} * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def scan_missing_subtitles_task() -> None:
    await media_tasks.scan_missing_subtitles()


_subtitles_scan_interval = max(
    MiraMediaConfig().subtitles.native.scan_interval_hours, 1
)
_STARTUP_SCHEDULES[scan_missing_subtitles_task.task_name] = [
    {"cron": f"0 */{_subtitles_scan_interval} * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def scheduled_library_scan_task() -> None:
    await media_tasks.scheduled_library_scan()


_imports_scan_interval = max(MiraMediaConfig().imports.auto_scan_interval_hours, 1)
_STARTUP_SCHEDULES[scheduled_library_scan_task.task_name] = [
    {"cron": f"0 */{_imports_scan_interval} * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def fulfill_approved_requests_task() -> None:
    await media_tasks.fulfill_approved_requests()


_requests_fulfill_interval = max(MiraMediaConfig().requests.fulfill_interval_hours, 1)
_STARTUP_SCHEDULES[fulfill_approved_requests_task.task_name] = [
    {"cron": f"0 */{_requests_fulfill_interval} * * *"}
]


def build_scheduler_loop() -> SchedulerLoop:
    source = PostgresqlSchedulerSource(
        dsn=_build_db_connection_string_for_taskiq,
        driver="psycopg",
        broker=broker,
        run_migrations=True,
        startup_schedule=_STARTUP_SCHEDULES,
    )
    scheduler = TaskiqScheduler(broker=broker, sources=[source])
    return SchedulerLoop(scheduler)


def _interval_cron(hours: int) -> str:
    if hours <= 0:
        hours = 1
    return f"0 */{hours} * * *"


def _import_sweep_cron() -> str:
    """Cron for background import-all-* sweeps (configurable minutes)."""
    mins = max(1, MiraMediaConfig().misc.import_sweep_interval_minutes)
    if mins >= 60:
        return _interval_cron(mins // 60)
    return f"*/{mins} * * * *"


def get_dynamic_schedule_targets() -> dict[str, str]:
    """Map of task_name -> cron expression for tasks whose cron is config-driven."""
    cfg = MiraMediaConfig()
    targets: dict[str, str] = {
        "miramedia.scheduler:auto_download_missing_episodes_task": _interval_cron(
            cfg.misc.auto_download_interval_hours
        ),
        "miramedia.scheduler:auto_download_missing_movies_task": _interval_cron(
            cfg.misc.auto_download_interval_hours
        ),
        "miramedia.scheduler:import_all_movie_torrents_task": _import_sweep_cron(),
        "miramedia.scheduler:import_all_show_torrents_task": _import_sweep_cron(),
    }
    targets["miramedia.scheduler:scan_missing_subtitles_task"] = _interval_cron(
        cfg.subtitles.native.scan_interval_hours
    )
    targets["miramedia.scheduler:scheduled_library_scan_task"] = _interval_cron(
        cfg.imports.auto_scan_interval_hours
    )
    targets["miramedia.scheduler:fulfill_approved_requests_task"] = _interval_cron(
        cfg.requests.fulfill_interval_hours
    )
    targets["miramedia.scheduler:check_for_updates_task"] = _interval_cron(
        cfg.updates.check_interval_hours
    )
    targets["miramedia.scheduler:verify_imported_files_task"] = _interval_cron(
        cfg.misc.integrity_check_interval_hours
    )
    return targets


async def refresh_dynamic_schedules() -> None:
    """Update the taskiq_schedulers rows in-place to match the current config."""
    from sqlalchemy import text

    from miramedia.database import SessionLocalBackground

    targets = get_dynamic_schedule_targets()
    if not targets:
        return

    async with SessionLocalBackground() as db:
        for task_name, new_cron in targets.items():
            try:
                await db.execute(
                    text(
                        "UPDATE taskiq_schedulers "
                        "SET schedule = jsonb_set(schedule, '{cron}', to_jsonb(:cron::text)), "
                        "    updated_at = NOW() "
                        "WHERE task_name = :task_name "
                        "  AND COALESCE(schedule->>'cron', '') <> :cron"
                    ),
                    {"cron": new_cron, "task_name": task_name},
                )
            except Exception:
                log.exception("Failed to refresh schedule for %s", task_name)
        await db.commit()

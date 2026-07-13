import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import taskiq_fastapi
from taskiq import TaskiqScheduler
from taskiq.cli.scheduler.run import SchedulerLoop
from taskiq_postgresql import PostgresqlBroker
from taskiq_postgresql.scheduler_source import PostgresqlSchedulerSource

from miramedia.config import MiraMediaConfig

# Bound concurrent SHA1 hashing so the integrity audit doesn't saturate disk I/O.
# Each compute_sha1 call streams the file in 1 MiB chunks — a large library can
# have thousands of multi-GiB files, and running them all in parallel would
# thrash the underlying storage and starve other async tasks of thread-pool
# slots. The default of 4 is conservative; tune via env var on faster arrays.
_SHA1_CONCURRENCY = max(1, int(os.getenv("MIRAMEDIA_SHA1_CONCURRENCY", "4")))
_SHA1_SEM: asyncio.Semaphore | None = None


def _get_sha1_semaphore() -> asyncio.Semaphore:
    """Lazy-init the semaphore so it's bound to the running event loop.

    Constructing it at module import would attach it to whichever loop happens
    to be current at import time, which causes "different loop" errors when
    the task body runs under the receiver's loop.
    """
    global _SHA1_SEM
    if _SHA1_SEM is None:
        _SHA1_SEM = asyncio.Semaphore(_SHA1_CONCURRENCY)
    return _SHA1_SEM


async def _compute_sha1_async(path: Path) -> str | None:
    """Offload sync SHA1 hashing to a worker thread under a concurrency cap."""
    from miramedia.torrents.integrity import compute_sha1

    sem = _get_sha1_semaphore()
    async with sem:
        return await asyncio.to_thread(compute_sha1, path)


def _build_db_connection_string_for_taskiq() -> str:
    from urllib.parse import quote

    from miramedia.config import MiraMediaConfig
    from miramedia.database import render_db_url

    db_config = MiraMediaConfig().database
    # taskiq's PostgresqlBroker passes the DSN straight to libpq, so it
    # wants a plain ``postgresql://...`` URL (no SQLAlchemy ``+driver``
    # prefix). Credentials are URL-encoded by SQLAlchemy.
    base = render_db_url(
        db_config.user,
        db_config.password,
        db_config.host,
        db_config.port,
        db_config.dbname,
        driver="psycopg_plain",
    )
    # EXEMPT broker sessions from ``idle_in_transaction_session_timeout``.
    #
    # The broker keeps a LISTEN connection parked indefinitely and a psycopg
    # pool of bookkeeping connections. psycopg defaults to non-autocommit, so
    # those connections sit ``idle in transaction`` between task bursts. The
    # server-wide ``idle_in_transaction_session_timeout`` we set in
    # docker-compose (leak protection for the *app* asyncpg pool) reaps the
    # broker's sockets after 5 min of quiet. psycopg's pool-2 then hands out
    # dead connections and refuses to reconnect for 300s, surfacing as
    # ``server closed the connection unexpectedly`` / ``SendTaskError: Cannot
    # send task to the queue`` — which silently kills scheduled tasks until a
    # restart. Disable the reaper for broker sessions ONLY via a libpq
    # per-connection ``options`` override (applies to both the pool and the
    # LISTEN connection since both consume this DSN). ``statement_timeout``
    # still bounds any runaway broker query, and the app pool keeps the
    # server-wide protection.
    libpq_options = quote("-c idle_in_transaction_session_timeout=0", safe="")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options={libpq_options}"


# NOTE: taskiq's PostgresqlBroker uses psycopg (sync) internally for its own
# task queue tables. This is intentional — only the broker's own bookkeeping
# runs through this connection. Our application code uses the async engine
# initialised in miramedia.database. Do not change the driver here.
#
# TWO-LANE BROKERS (priority isolation)
# -------------------------------------
# taskiq's Receiver has NO per-message filter API — it consumes everything
# that broker.listen() yields. PostgresqlBroker.listen() atomically claims
# rows via ``DELETE … RETURNING``, so two Receivers attached to one broker
# would race and steal each other's messages. To keep user-triggered tasks
# (manual add, manual scan, manual resolve) from queueing behind a long
# background sweep (auto-import, sha1 audit, metadata refresh), we run two
# fully separate brokers — each with its own NOTIFY channel, its own
# ``taskiq_messages_*`` row queue, and its own Receiver with a dedicated
# ``max_async_tasks`` budget. Tasks are bound at decoration time to the
# broker that matches their priority; the receiver-side filtering is
# therefore implicit (each receiver only ever sees its own broker's rows).
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

# Back-compat alias: external callers / older imports reference ``broker``.
# Routing of `.kiq()` calls is now done at task-definition time (each task is
# decorated on its specific lane broker), so this alias is only used for
# things like the FastAPI dependency context init and the scheduler-source
# (which lives on the background broker because every scheduled cron task
# fans out on the background lane).
broker = background_broker

# Register FastAPI app with BOTH brokers so worker processes can resolve
# FastAPI dependencies regardless of which lane executes a task. Using a
# string reference avoids circular imports.
taskiq_fastapi.init(interactive_broker, "miramedia.main:app")
taskiq_fastapi.init(background_broker, "miramedia.main:app")

log = logging.getLogger(__name__)


# Serialise the import sweeps so overlapping ticks never race on the same
# torrent. The sweep is cron'd every minute AND re-dispatched by
# ``detect_finished_downloads_task`` the moment a download finishes; a large
# cross-volume copy on the NAS can outlast the 1-min interval, so a second
# invocation would import the same files concurrently — producing
# ``LockNotAvailableError`` on the per-file UPDATE, "reappeared during link"
# conflicts, and source-file-gone errors. Worse, when the winning attempt
# cleans up the torrent (FK ``ON DELETE SET NULL``) the loser then stamps an
# already-imported episode ``failed_io`` with no torrent left to surface it on
# the imports page — an invisible "ghost" failure inflating the dashboard
# badge. A non-blocking guard turns an overlapping tick into a no-op; the next
# free tick picks up any remaining work. Lazy per-loop init mirrors
# ``_get_sha1_semaphore`` (a module-import-time lock binds to the wrong loop).
_IMPORT_SWEEP_LOCKS: dict[str, asyncio.Lock] = {}


def _import_sweep_lock(key: str) -> asyncio.Lock:
    lock = _IMPORT_SWEEP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _IMPORT_SWEEP_LOCKS[key] = lock
    return lock


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
    # Pagination note: the candidate set is already narrowed by the
    # service via status=='finished' AND is_due_for_retry(...) (exp backoff
    # 1→120m on attempt_count). On a healthy library this filters down to
    # a handful of rows per minute-cron tick.
    lock = _import_sweep_lock("movie")
    if lock.locked():
        log.debug("Movie import sweep already running; skipping overlapping tick")
        return
    async with lock:
        from miramedia.database import bg_movie_service

        async with bg_movie_service() as movie_service:
            await movie_service.import_all_torrents()
    # Broadcast a refresh hint so connected SSE clients re-query the
    # torrents + imports endpoints without waiting for their backstop poll.
    from miramedia.events.bus import Event, get_event_bus

    get_event_bus().publish(Event(type="torrent.refresh"))


@background_broker.task(labels={"priority": "background"})
async def import_all_show_torrents_task() -> None:
    lock = _import_sweep_lock("show")
    if lock.locked():
        log.debug("Show import sweep already running; skipping overlapping tick")
        return
    async with lock:
        from miramedia.database import bg_show_service

        async with bg_show_service() as show_service:
            await show_service.import_all_torrents()
    from miramedia.events.bus import Event, get_event_bus

    get_event_bus().publish(Event(type="torrent.refresh"))


@background_broker.task(labels={"priority": "background"})
async def detect_finished_downloads_task() -> None:
    """Promptly notice downloads that just finished and trigger their import.

    The download client is polled only on demand (torrents list / detail
    fetch, both ``persist=False``), so a torrent that completes while nobody is
    looking keeps a stale ``downloading`` status — and the imports queue, gated
    on ``finished`` (see ``TorrentService.is_import_ready``), would not show it
    until the 5-min import sweep. This 1-min sweep fans out live status over
    just the *active* downloads (not the whole library) and, if any have
    finished, kicks the import sweeps so the row appears within ~1 minute.
    """
    from miramedia.database import (
        bg_torrent_service,
        release_session_before_external_io,
    )
    from miramedia.torrents.schemas import TorrentStatus

    async with bg_torrent_service() as svc:
        torrents = await svc.torrent_repository.get_active_torrents()
        if not torrents:
            return
        # Release the DB connection before the per-torrent client RPC fan-out
        # so the session never sits idle-in-transaction across external I/O.
        await release_session_before_external_io(svc.torrent_repository.db)
        live = await svc._fetch_live_torrent_statuses(torrents)
        newly_finished = any(t.status == TorrentStatus.finished for t in live)

    if newly_finished:
        await import_all_movie_torrents_task.kiq()
        await import_all_show_torrents_task.kiq()


@background_broker.task(labels={"priority": "background"})
async def update_all_movies_metadata_task() -> None:
    from miramedia.movies.service import (
        _auto_download_missing_movies_impl,
        _update_all_movies_metadata_impl,
    )

    # Both helpers open their own per-iteration ``bg_movie_service``
    # sessions — keep no outer wrapper session, otherwise it would sit
    # idle-in-TX through hundreds of provider HTTP calls + the slow
    # indexer fan-out.
    await _update_all_movies_metadata_impl()
    await _auto_download_missing_movies_impl()


@background_broker.task(labels={"priority": "background"})
async def update_all_shows_metadata_task() -> None:
    from miramedia.shows.service import (
        _auto_download_missing_episodes_impl,
        _update_all_shows_metadata_impl,
    )

    # Both helpers open their own per-iteration ``bg_show_service``
    # sessions — keep no outer wrapper session, otherwise it would sit
    # idle-in-TX through hundreds of provider HTTP calls + the slow
    # indexer fan-out.
    await _update_all_shows_metadata_impl()
    await _auto_download_missing_episodes_impl()


@background_broker.task(labels={"priority": "background"})
async def auto_download_missing_episodes_task() -> None:
    from miramedia.shows.service import _auto_download_missing_episodes_impl

    log.info("Running auto-download for shows with continuous download enabled")
    # Helper manages its own per-iteration ``bg_show_service`` sessions; no
    # outer wrapper session is needed.
    await _auto_download_missing_episodes_impl()


@background_broker.task(labels={"priority": "background"})
async def auto_download_missing_movies_task() -> None:
    from miramedia.movies.service import _auto_download_missing_movies_impl

    log.info("Running auto-download for movies with continuous download enabled")
    # Helper manages its own per-iteration ``bg_movie_service`` sessions; no
    # outer wrapper session is needed.
    await _auto_download_missing_movies_impl()


@interactive_broker.task(labels={"priority": "interactive"})
async def add_show_task(
    external_id: str,
    metadata_provider_name: str,
    language: str | None = None,
) -> None:
    """Background add-show: fetches metadata, persists, triggers auto-download.

    Surfaces in the imports / shows list once complete. Wrapped in a task so
    the HTTP request for the add action returns immediately and the UI stays
    interactive while the (potentially slow) metadata fetch runs.

    Auto-download is intentionally deferred to AFTER the outer
    ``bg_show_service`` session is closed: the indexer fan-out can take
    minutes (cloudflare bypass + parallel HTTP across sites), and holding
    the add-show session open through that fan-out previously left the
    connection ``idle in transaction`` long enough for Postgres
    ``idle_in_transaction_session_timeout`` to kill it, surfacing as
    ``InterfaceError: connection is closed`` from the indexer save_result.
    """
    from miramedia.config import MiraMediaConfig
    from miramedia.database import bg_show_service
    from miramedia.exceptions import MediaAlreadyExistsError
    from miramedia.metadata.dependencies import get_metadata_provider
    from miramedia.shows.service import _try_auto_download_show_id_impl

    saved_id = None
    should_auto_download = False
    try:
        provider = get_metadata_provider(metadata_provider_name)
        async with bg_show_service() as show_service:
            saved = await show_service.add_show(
                external_id=external_id,
                metadata_provider=provider,
                language=language,
            )
            saved_id = saved.id
            global_cd = MiraMediaConfig().misc.continuous_download
            effective_cd = (
                saved.continuous_download
                if saved.continuous_download is not None
                else global_cd
            )
            should_auto_download = bool(effective_cd) and not saved.skipped
        # Show-added log is emitted inside add_show, before
        # auto-download fans out, so it appears in the right place
        # relative to the auto-download chatter.
    except MediaAlreadyExistsError:
        log.info(
            "Show %s already exists in library; add was a no-op",
            external_id,
        )
    except Exception as exc:
        log.exception(
            "Failed to add show %s via %s", external_id, metadata_provider_name
        )
        _notify_add_failure("show", external_id, exc)
        return

    if saved_id is not None and should_auto_download:
        await _try_auto_download_show_id_impl(saved_id)


@interactive_broker.task(labels={"priority": "interactive"})
async def add_movie_task(
    external_id: str,
    metadata_provider_name: str,
    language: str | None = None,
) -> None:
    """Background add-movie. Mirror of add_show_task.

    Auto-download is intentionally deferred to AFTER the outer
    ``bg_movie_service`` session is closed — see ``add_show_task`` for
    the rationale.
    """
    from miramedia.config import MiraMediaConfig
    from miramedia.database import bg_movie_service
    from miramedia.exceptions import ConflictError
    from miramedia.metadata.dependencies import get_metadata_provider
    from miramedia.movies.service import _try_auto_download_movie_id_impl

    saved_id = None
    should_auto_download = False
    try:
        provider = get_metadata_provider(metadata_provider_name)
        async with bg_movie_service() as movie_service:
            saved = await movie_service.add_movie(
                external_id=external_id,
                metadata_provider=provider,
                language=language,
            )
            saved_id = saved.id
            global_cd = MiraMediaConfig().misc.continuous_download
            effective_cd = (
                saved.continuous_download
                if saved.continuous_download is not None
                else global_cd
            )
            should_auto_download = bool(effective_cd) and not saved.skipped
    except ConflictError:
        log.info(
            "Movie %s already exists in library; add was a no-op",
            external_id,
        )
    except Exception as exc:
        log.exception(
            "Failed to add movie %s via %s", external_id, metadata_provider_name
        )
        _notify_add_failure("movie", external_id, exc)
        return

    if saved_id is not None and should_auto_download:
        await _try_auto_download_movie_id_impl(saved_id)


def _notify_add_failure(kind: str, external_id: str, exc: Exception) -> None:
    """Surface a background add failure through the in-app notification
    system. Without this the user clicks Add → sees "Queued" toast → no
    library entry appears → no signal that anything went wrong.

    Stays sync — NotificationManager.send_notification only fans out to
    external HTTP providers; it does not touch the project DB."""
    try:
        from miramedia.notifications.manager import NotificationManager

        NotificationManager().send_notification(
            title=f"Could not add {kind}",
            message=f"{external_id}: {exc}",
        )
    except Exception:
        log.debug("Failed to surface add-failure notification", exc_info=True)


# Maps each task to its cron schedule so PostgresqlSchedulerSource can seed
# the taskiq_schedulers table on first startup.
_STARTUP_SCHEDULES: dict[str, list[dict[str, str]]] = {
    # Cron resolution is 1 minute (Phase 6 — the resolved-decision target was
    # 30s, which would need taskiq interval-scheduling instead of cron; the
    # exponential-backoff filter on per-file ``attempt_count`` already prevents
    # tight retry loops on persistently-failing torrents).
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


# Grace window before an unstarted queued row may be automatically reclaimed.


@background_broker.task(labels={"priority": "background"})
async def reclaim_stale_queued_imports_task() -> None:
    """Recover scan rows wedged in "queued" because their worker died without
    a restart (the common restart case is handled at startup). Without this a
    library-scan import can show "Importing" forever and the progress toast
    never drains."""
    from miramedia.database import SessionLocalBackground
    from miramedia.imports.repository import (
        STALE_QUEUED_IMPORT_GRACE,
        ImportsRepository,
    )

    async with SessionLocalBackground() as db:
        reclaimed = await ImportsRepository(db).reclaim_stale_queued_imports(
            older_than=STALE_QUEUED_IMPORT_GRACE
        )
    if reclaimed:
        log.warning("Reclaimed %d stale queued import(s)", reclaimed)


_STARTUP_SCHEDULES[reclaim_stale_queued_imports_task.task_name] = [
    {"cron": "*/10 * * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def cleanup_old_logs_task() -> None:
    from miramedia.database import SessionLocalBackground
    from miramedia.logs.repository import LogRepository

    retention_days = MiraMediaConfig().misc.log_retention_days
    log.info(f"Cleaning up activity logs older than {retention_days} days")
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with SessionLocalBackground() as db:
        deleted = await LogRepository(db).delete_older_than(cutoff)
        await db.commit()
    log.info("Deleted %d old activity log entries", deleted)


_STARTUP_SCHEDULES[cleanup_old_logs_task.task_name] = [{"cron": "0 3 * * *"}]

POSTER_VARIANT_WIDTHS = (200, 300, 400, 600, 800)
_POSTER_VARIANT_MAX_BYTES = 512 * 1024 * 1024


def _poster_source_for_variant(image_dir: Path, variant_path: Path) -> Path | None:
    stem = variant_path.stem
    suffix = variant_path.suffix
    for width in POSTER_VARIANT_WIDTHS:
        width_suffix = f"-{width}"
        if stem.endswith(width_suffix):
            source_stem = stem[: -len(width_suffix)]
            return image_dir / f"{source_stem}{suffix}"
    return None


def _variant_access_time(path: Path) -> float:
    stat = path.stat()
    return stat.st_atime if stat.st_atime > 0 else stat.st_mtime


def evict_poster_variants(
    image_dir: Path,
    variant_dir: Path,
    *,
    max_total_bytes: int = _POSTER_VARIANT_MAX_BYTES,
) -> list[Path]:
    """Delete orphaned poster variants and enforce a total size cap."""
    if not variant_dir.is_dir():
        return []

    deleted: list[Path] = []
    remaining: list[tuple[Path, int]] = []

    for variant_path in variant_dir.iterdir():
        if not variant_path.is_file():
            continue
        source = _poster_source_for_variant(image_dir, variant_path)
        if source is None or not source.is_file():
            try:
                variant_path.unlink()
                deleted.append(variant_path)
            except OSError:
                log.debug(
                    "failed to delete orphaned poster variant %s",
                    variant_path,
                    exc_info=True,
                )
            continue
        remaining.append((variant_path, variant_path.stat().st_size))

    total = sum(size for _, size in remaining)
    if total <= max_total_bytes:
        return deleted

    remaining.sort(key=lambda item: _variant_access_time(item[0]))
    for variant_path, size in remaining:
        if total <= max_total_bytes:
            break
        try:
            variant_path.unlink()
            deleted.append(variant_path)
            total -= size
        except OSError:
            log.debug(
                "failed to delete poster variant %s during eviction",
                variant_path,
                exc_info=True,
            )

    return deleted


@background_broker.task(labels={"priority": "background"})
async def cleanup_poster_variants_task() -> None:
    image_dir = MiraMediaConfig().misc.image_directory
    variant_dir = image_dir / ".variants"
    log.info("Cleaning up poster variant cache in %s", variant_dir)
    deleted = await asyncio.to_thread(evict_poster_variants, image_dir, variant_dir)
    log.info("Deleted %d poster variant file(s)", len(deleted))


_STARTUP_SCHEDULES[cleanup_poster_variants_task.task_name] = [{"cron": "30 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def purge_old_indexer_query_results_task() -> None:
    """Delete stale indexer search cache rows (unreferenced after download)."""
    from sqlalchemy import delete

    from miramedia.database import SessionLocalBackground
    from miramedia.indexers.models import IndexerQueryResult

    retention_days = MiraMediaConfig().misc.indexer_query_result_retention_days
    if retention_days <= 0:
        return
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    log.info("Purging indexer_query_result rows older than %d days", retention_days)
    async with SessionLocalBackground() as db:
        result = await db.execute(
            delete(IndexerQueryResult).where(IndexerQueryResult.created_at < cutoff)
        )
        await db.commit()
        deleted = result.rowcount or 0
    log.info("Purged %d indexer query result rows", deleted)


_STARTUP_SCHEDULES[purge_old_indexer_query_results_task.task_name] = [
    {"cron": "15 3 * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def cleanup_old_notifications_task() -> None:
    """Delete read in-app notifications older than ``notifications.native.retention_days``."""
    from miramedia.database import SessionLocalBackground
    from miramedia.notifications.repository import NotificationRepository

    cfg = MiraMediaConfig().notifications.native
    if not cfg.enabled:
        return
    retention_days = cfg.retention_days
    log.info(f"Cleaning up read notifications older than {retention_days} days")
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with SessionLocalBackground() as db:
        deleted = await NotificationRepository(db).delete_read_older_than(cutoff)
        await db.commit()
    log.info("Deleted %d old read notifications", deleted)


_STARTUP_SCHEDULES[cleanup_old_notifications_task.task_name] = [{"cron": "0 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def cleanup_expired_manual_parse_tokens_task() -> None:
    from miramedia.database import SessionLocalBackground
    from miramedia.torrents.repository import TorrentRepository

    async with SessionLocalBackground() as db:
        deleted = await TorrentRepository(db).delete_expired_manual_parse_tokens(
            ttl_minutes=30
        )
        await db.commit()
    if deleted:
        log.info("Deleted %d expired manual parse tokens", deleted)


_STARTUP_SCHEDULES[cleanup_expired_manual_parse_tokens_task.task_name] = [
    {"cron": "*/15 * * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def save_native_resume_data_task() -> None:
    """Periodically snapshot libtorrent resume data to disk.

    The native client only persists resume data at graceful shutdown. An
    ungraceful kill (OOM, ``docker kill``, host crash) would otherwise lose
    all in-flight torrents — on restart they're never re-added to the session
    and every pause/resume/remove logs "Torrent not found". Checkpointing on a
    cron lets the next start recover torrents added since the last clean stop.
    """
    cfg = MiraMediaConfig().torrents.native
    if not cfg.enabled:
        return
    from miramedia.torrents.backends.native import NativeDownloadClient

    # save_resume_data() is a blocking libtorrent call (it pumps the alert
    # queue with sleeps) — run it off the event loop.
    await asyncio.to_thread(NativeDownloadClient().save_resume_data)


_STARTUP_SCHEDULES[save_native_resume_data_task.task_name] = [{"cron": "*/5 * * * *"}]


@background_broker.task(labels={"priority": "background"})
async def purge_old_taskiq_messages_task() -> None:
    """Delete stranded taskiq message rows older than 7 days.

    Each lane broker owns its own queue table — ``taskiq_messages_interactive``
    and ``taskiq_messages_background`` (see the ``table_name=`` overrides on the
    broker constructions above). There is NO bare ``taskiq_messages`` table, so
    we purge each broker's actual ``table_name`` rather than a hardcoded name.

    PostgresqlBroker stores each enqueued message as a row in its queue table;
    the listening worker atomically removes it via
    ``DELETE ... RETURNING`` when it claims the message (see
    ``taskiq_postgresql.broker.PostgresqlBroker.listen``). The table
    therefore should only contain rows that were enqueued but never
    claimed — either because no worker was running at the time or because
    a NOTIFY was lost. On a long-running instance these orphaned rows
    accumulate; this nightly purge bounds the growth.

    Schema reference (taskiq_postgresql 0.x):
        id           SERIAL PRIMARY KEY
        task_id      UUID
        task_name    VARCHAR
        message      BYTEA
        labels       JSONB
        created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()

    There is no ``state``/``status`` column — completed/failed rows are
    physically deleted on claim. We therefore filter on ``created_at``
    alone; anything older than the retention window is orphaned by
    definition.
    """
    from sqlalchemy import text

    from miramedia.database import SessionLocalBackground

    # Table names come from our own broker config constants, never user input,
    # so interpolating them as identifiers is safe.
    table_names = {b.table_name for b in (interactive_broker, background_broker)}

    async with SessionLocalBackground() as db:
        for table_name in table_names:
            try:
                result = await db.execute(
                    text(
                        f"DELETE FROM {table_name} "  # noqa: S608 (trusted identifier)
                        "WHERE created_at < NOW() - INTERVAL '7 days'"
                    )
                )
                await db.commit()
                deleted = result.rowcount or 0
                if deleted:
                    log.info("Purged %d stale rows from %s", deleted, table_name)
            except Exception:
                log.exception("Failed to purge %s", table_name)
                await db.rollback()


_STARTUP_SCHEDULES[purge_old_taskiq_messages_task.task_name] = [{"cron": "30 3 * * *"}]


@background_broker.task(labels={"priority": "background"})
async def verify_imported_files_task() -> None:
    """Lazy SHA1 baseline + integrity audit for imported files.

    First pass over a row populates ``sha1``; subsequent passes recompute and
    log a WARNING (and stamp ``import_error``) on mismatch. Skipped entirely
    when ``misc.integrity_check_enabled`` is off.

    Session lifetime: we snapshot the row PKs + paths under a short session,
    drop the session, hash each file off-pool, then re-open a short session
    per-batch to persist results. Previously the session was held open for
    the entire hash sweep — multi-hour walltime on a large library, pinning
    one connection ``idle in transaction`` the whole time.
    """
    from miramedia.config import MiraMediaConfig

    if not MiraMediaConfig().misc.integrity_check_enabled:
        return

    from sqlalchemy import func, select

    from miramedia.database import background_session
    from miramedia.file_status import ImportOutcome
    from miramedia.movies.models import MovieFile
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.schemas import MovieFile as MovieFileSchema
    from miramedia.shows.models import EpisodeFile
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
    from miramedia.torrents.integrity import (
        INTEGRITY_AUDIT_CHUNK_SIZE,
        IntegrityPathLayout,
        batch_resolve_episode_paths_async,
        batch_resolve_movie_paths_async,
    )

    baselined = 0
    verified = 0
    mismatched = 0
    skipped_stale = 0

    async def _apply_episode_result(
        show_repo: ShowRepository,
        file_id: uuid.UUID,
        prior: str | None,
        prior_error: str | None,
        sha: str,
        target: Path,
    ) -> None:
        nonlocal baselined, verified, mismatched, skipped_stale
        if prior is None:
            if await show_repo.apply_integrity_baseline_if_current(
                file_id,
                expected_sha1=None,
                expected_import_error=prior_error,
                new_sha1=sha,
            ):
                baselined += 1
            else:
                skipped_stale += 1
                log.debug(
                    "integrity audit: skipped stale baseline for episode_file %s",
                    file_id,
                )
        elif prior != sha:
            mismatch_error = f"sha1 mismatch (expected {prior[:10]}…, got {sha[:10]}…)"
            if await show_repo.stamp_integrity_mismatch_if_current(
                file_id,
                expected_sha1=prior,
                expected_import_error=prior_error,
                import_error=mismatch_error,
            ):
                mismatched += 1
                log.warning(
                    "integrity audit: episode_file sha1 mismatch %s (%s)",
                    target,
                    file_id,
                )
            else:
                skipped_stale += 1
                log.debug(
                    "integrity audit: skipped stale mismatch for episode_file %s",
                    file_id,
                )
        else:
            verified += 1

    async def _apply_movie_result(
        movie_repo: MovieRepository,
        file_id: uuid.UUID,
        prior: str | None,
        prior_error: str | None,
        sha: str,
        target: Path,
    ) -> None:
        nonlocal baselined, verified, mismatched, skipped_stale
        if prior is None:
            if await movie_repo.apply_integrity_baseline_if_current(
                file_id,
                expected_sha1=None,
                expected_import_error=prior_error,
                new_sha1=sha,
            ):
                baselined += 1
            else:
                skipped_stale += 1
                log.debug(
                    "integrity audit: skipped stale baseline for movie_file %s",
                    file_id,
                )
        elif prior != sha:
            mismatch_error = f"sha1 mismatch (expected {prior[:10]}…, got {sha[:10]}…)"
            if await movie_repo.stamp_integrity_mismatch_if_current(
                file_id,
                expected_sha1=prior,
                expected_import_error=prior_error,
                import_error=mismatch_error,
            ):
                mismatched += 1
                log.warning(
                    "integrity audit: movie_file sha1 mismatch %s (%s)",
                    target,
                    file_id,
                )
            else:
                skipped_stale += 1
                log.debug(
                    "integrity audit: skipped stale mismatch for movie_file %s",
                    file_id,
                )
        else:
            verified += 1

    layout = IntegrityPathLayout.from_config()

    ep_max_id: uuid.UUID | None = None
    ep_budget = 0
    mv_max_id: uuid.UUID | None = None
    mv_budget = 0
    async with background_session() as db:
        ep_max_id = (
            await db.execute(
                select(EpisodeFile.id)
                .where(EpisodeFile.import_status == ImportOutcome.imported)
                .order_by(EpisodeFile.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        ep_budget = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(EpisodeFile)
                    .where(EpisodeFile.import_status == ImportOutcome.imported)
                )
            ).scalar_one()
        )
        mv_max_id = (
            await db.execute(
                select(MovieFile.id)
                .where(MovieFile.import_status == ImportOutcome.imported)
                .order_by(MovieFile.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        mv_budget = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(MovieFile)
                    .where(MovieFile.import_status == ImportOutcome.imported)
                )
            ).scalar_one()
        )

    last_episode_id = uuid.UUID(int=0)
    remaining_ep_budget = ep_budget
    if remaining_ep_budget > 0 and ep_max_id is not None:
        while remaining_ep_budget > 0:
            row_snapshots: list[tuple] = []
            episode_context = {}
            shows = {}
            ep_schema_rows: list[EpisodeFileSchema] = []
            chunk_limit = min(INTEGRITY_AUDIT_CHUNK_SIZE, remaining_ep_budget)
            async with background_session() as db:
                ep_result = await db.execute(
                    select(EpisodeFile)
                    .where(
                        EpisodeFile.import_status == ImportOutcome.imported,
                        EpisodeFile.id > last_episode_id,
                        EpisodeFile.id <= ep_max_id,
                    )
                    .order_by(EpisodeFile.id)
                    .limit(chunk_limit)
                )
                ep_rows = ep_result.scalars().all()
                if not ep_rows:
                    break
                last_episode_id = ep_rows[-1].id
                remaining_ep_budget -= len(ep_rows)
                show_repo = ShowRepository(db)
                ep_schema_rows = [
                    EpisodeFileSchema.model_validate(row) for row in ep_rows
                ]
                episode_context = await show_repo.batch_episodes_with_context(
                    [
                        row.episode_id
                        for row in ep_schema_rows
                        if row.episode_id is not None
                    ]
                )
                shows = await show_repo.get_shows_by_ids(
                    list({ctx.show_id for ctx in episode_context.values()})
                )
                row_snapshots = [
                    (row.id, row.sha1, row.import_error, row) for row in ep_rows
                ]

            paths = await batch_resolve_episode_paths_async(
                ep_schema_rows,
                episode_context,
                shows,
                layout,
            )

            chunk_targets: list[tuple] = []
            for file_id, prior, prior_error, _row in row_snapshots:
                target = paths.get(file_id)
                if target is None or not target.exists():
                    continue
                chunk_targets.append((file_id, prior, prior_error, target))

            chunk_results: list[tuple] = []
            for file_id, prior, prior_error, target in chunk_targets:
                sha = await _compute_sha1_async(target)
                if sha is None:
                    continue
                chunk_results.append((file_id, prior, prior_error, sha, target))

            async with background_session() as db:
                show_repo = ShowRepository(db)
                for file_id, prior, prior_error, sha, target in chunk_results:
                    await _apply_episode_result(
                        show_repo, file_id, prior, prior_error, sha, target
                    )
                await db.commit()

    last_movie_id = uuid.UUID(int=0)
    remaining_mv_budget = mv_budget
    if remaining_mv_budget > 0 and mv_max_id is not None:
        while remaining_mv_budget > 0:
            row_snapshots = []
            movies = {}
            mv_schema_rows: list[MovieFileSchema] = []
            chunk_limit = min(INTEGRITY_AUDIT_CHUNK_SIZE, remaining_mv_budget)
            async with background_session() as db:
                mv_result = await db.execute(
                    select(MovieFile)
                    .where(
                        MovieFile.import_status == ImportOutcome.imported,
                        MovieFile.id > last_movie_id,
                        MovieFile.id <= mv_max_id,
                    )
                    .order_by(MovieFile.id)
                    .limit(chunk_limit)
                )
                mv_rows = mv_result.scalars().all()
                if not mv_rows:
                    break
                last_movie_id = mv_rows[-1].id
                remaining_mv_budget -= len(mv_rows)
                movie_repo = MovieRepository(db)
                mv_schema_rows = [
                    MovieFileSchema.model_validate(row) for row in mv_rows
                ]
                movies = await movie_repo.get_movies_by_ids(
                    [row.movie_id for row in mv_schema_rows if row.movie_id is not None]
                )
                row_snapshots = [
                    (row.id, row.sha1, row.import_error, row) for row in mv_rows
                ]

            paths = await batch_resolve_movie_paths_async(
                mv_schema_rows,
                movies,
                layout,
            )

            chunk_targets = []
            for file_id, prior, prior_error, _row in row_snapshots:
                target = paths.get(file_id)
                if target is None or not target.exists():
                    continue
                chunk_targets.append((file_id, prior, prior_error, target))

            chunk_results = []
            for file_id, prior, prior_error, target in chunk_targets:
                sha = await _compute_sha1_async(target)
                if sha is None:
                    continue
                chunk_results.append((file_id, prior, prior_error, sha, target))

            async with background_session() as db:
                movie_repo = MovieRepository(db)
                for file_id, prior, prior_error, sha, target in chunk_results:
                    await _apply_movie_result(
                        movie_repo, file_id, prior, prior_error, sha, target
                    )
                await db.commit()

    log.info(
        "integrity audit: %d baselined, %d verified, %d MISMATCH, %d stale skipped",
        baselined,
        verified,
        mismatched,
        skipped_stale,
    )
    if mismatched:
        # Surface the new corrupt-file rows on the imports page without waiting
        # for the next full queue rebuild.
        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()


_integrity_interval_hours = max(
    1, MiraMediaConfig().misc.integrity_check_interval_hours
)
_STARTUP_SCHEDULES[verify_imported_files_task.task_name] = [
    {"cron": f"0 */{_integrity_interval_hours} * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def check_for_updates_task() -> None:
    cfg = MiraMediaConfig().updates
    if not cfg.enabled:
        return
    from miramedia.updates.service import UpdateService

    svc = UpdateService()
    # get_update_info is SYNC and makes a blocking GitHub HTTP call. Awaiting
    # it raised ``TypeError: object UpdateInfo can't be used in 'await'
    # expression`` every run; calling it inline would also block the scheduler
    # event loop (and stall the per-minute schedule tick, tripping taskiq's
    # "schedules getting task started before the previous one finished"
    # warnings). Offload to a worker thread.
    info = await asyncio.to_thread(svc.get_update_info, True)
    if info.update_available:
        log.info(
            "update available: %s -> %s (%s)",
            info.current_version,
            info.latest_version,
            info.release_url,
        )
        if cfg.notify_on_new_version:
            _notify_update_available(info)


def _notify_update_available(info) -> None:  # noqa: ANN001
    try:
        from miramedia.notifications.manager import NotificationManager

        title = "MiraMedia update available"
        message = (
            f"New version {info.latest_version} available "
            f"(current {info.current_version}). {info.release_url or ''}"
        ).strip()
        NotificationManager().send_notification(title=title, message=message)
    except Exception:
        log.exception("failed to dispatch update-available notification")


_update_interval = MiraMediaConfig().updates.check_interval_hours
_STARTUP_SCHEDULES[check_for_updates_task.task_name] = [
    {"cron": f"0 */{max(1, _update_interval)} * * *"}
]

# NOTE: the subtitles / imports-scan / requests tasks below are registered
# UNCONDITIONALLY and self-guard on the live config singleton (the settings
# save path updates it in-memory via apply_overrides_to_config). Enabling or
# disabling these features therefore takes effect WITHOUT a restart; interval
# changes are re-synced live by refresh_dynamic_schedules(). This mirrors the
# always-registered, guard-inside pattern of check_for_updates_task /
# cleanup_old_notifications_task. The cron rows always exist, so a disabled
# feature simply fires a task that early-returns (cheap no-op).
# Deferred (E402) to avoid a circular import: these modules import from
# scheduler, so they can only be pulled in after the task definitions above.
from miramedia.metadata.dependencies import resolve_metadata_provider  # noqa: E402
from miramedia.requests.dependencies import build_seerr_client  # noqa: E402
from miramedia.requests.schemas import RequestStatus  # noqa: E402
from miramedia.requests.sync import SeerrSyncService  # noqa: E402


@background_broker.task(labels={"priority": "background"})
async def scan_missing_subtitles_task() -> None:
    from miramedia.database import bg_subtitle_service

    cfg = MiraMediaConfig().subtitles
    if not (cfg.enabled and cfg.native.enabled):
        return
    log.info("Running scheduled subtitle scan")
    # ``scan_all_missing_subtitles`` itself opens fresh per-item
    # ``bg_subtitle_service()`` for each episode/movie. The outer service we
    # build here is used only as the entry-point — its session is closed as
    # soon as the inner walk pivots to per-item sessions.
    async with bg_subtitle_service() as subtitle_service:
        await subtitle_service.scan_all_missing_subtitles()


_subtitles_scan_interval = max(
    MiraMediaConfig().subtitles.native.scan_interval_hours, 1
)
_STARTUP_SCHEDULES[scan_missing_subtitles_task.task_name] = [
    {"cron": f"0 */{_subtitles_scan_interval} * * *"}
]


@background_broker.task(labels={"priority": "background"})
async def scheduled_library_scan_task() -> None:
    """Library scan task.

    ``_scan_and_cache`` owns its own short-lived bg sessions per phase
    (mark-running, walk, read snapshot, auto-import-per-item, writeback).
    No outer bg-service wrapper here — that previously held three pool
    connections in ``idle in transaction`` for the entire multi-minute scan.
    """
    if not MiraMediaConfig().imports.auto_scan_enabled:
        return
    log.info("Running scheduled library scan")
    from miramedia.imports.tasks import _scan_and_cache

    await _scan_and_cache()


_imports_scan_interval = max(MiraMediaConfig().imports.auto_scan_interval_hours, 1)
_STARTUP_SCHEDULES[scheduled_library_scan_task.task_name] = [
    {"cron": f"0 */{_imports_scan_interval} * * *"}
]


# Cron-dominant; manual admin-trigger from /api/v1/requests also enqueues this
# task but the cron tick rules — keep it on the background lane to avoid a
# multi-request reconcile starving the interactive budget.
@background_broker.task(labels={"priority": "background"})
async def fulfill_approved_requests_task() -> None:
    """Fulfil approved media requests.

    Each major step (Seerr reconcile, listing approved requests, per-request
    add_movie / add_show + per-episode downloaded check) now uses its own
    short-lived ``bg_*_service()`` context so a slow metadata-provider HTTP
    call doesn't pin a connection ``idle in transaction`` for the whole task.
    """
    from miramedia.database import bg_movie_service, bg_request_service, bg_show_service

    if not MiraMediaConfig().requests.enabled:
        return

    # Seerr reconcile gets its own short-lived session.
    seerr_client = build_seerr_client()
    if seerr_client is not None:
        try:
            async with bg_request_service() as (_, request_repository):
                await SeerrSyncService(request_repository, seerr_client).reconcile()
        except Exception:
            log.exception("Seerr reconcile failed")
        finally:
            await seerr_client.aclose()

    log.info("Checking for approved requests to download")
    async with bg_request_service() as (request_service, _):
        approved = await request_service.get_approved_not_downloaded()
    if not approved:
        return
    log.info(f"Found {len(approved)} approved requests not yet downloaded")

    for request in approved:
        try:
            # Fresh (approved) requests get dispatched + fanned out to indexers.
            # Already-dispatched (downloading) requests are only re-checked for
            # completion below — no second fan-out, so a slow download isn't
            # re-searched across 7 sites every cycle (and a manual approve that
            # re-kicks this task can't re-dispatch an in-flight request).
            is_fresh = request.status == RequestStatus.approved
            provider_name = request.metadata_provider or "native"
            metadata_provider = resolve_metadata_provider(provider_name)
            if metadata_provider is None:
                log.warning(
                    f"No metadata provider available for request: {request.title}"
                )
                continue

            # Native provider requires IMDb IDs (tt...). Swap to imdb_id
            # whenever the resolved provider is native and external_id
            # isn't already an IMDb ID.
            effective_id = request.external_id
            if metadata_provider.name == "native" and not effective_id.startswith("tt"):
                if not request.imdb_id:
                    async with bg_request_service() as (request_service, _):
                        request = await request_service.heal_missing_imdb_id(request)
                if request.imdb_id:
                    effective_id = request.imdb_id
                else:
                    log.warning(
                        f"Cannot fulfill request {request.title}: "
                        f"native provider requires IMDb ID but request has "
                        f"external_id={request.external_id} and no imdb_id stored"
                    )
                    continue

            if request.media_type.value == "movie":
                from miramedia.movies.service import (
                    _try_auto_download_movie_id_impl,
                )

                async with bg_movie_service() as movie_service:
                    movie = await movie_service.add_movie(
                        external_id=effective_id,
                        metadata_provider=metadata_provider,
                    )
                if is_fresh:
                    # Auto-download deferred to a fresh short-lived session
                    # so the indexer fan-out can't pin the add session past
                    # idle_in_transaction_session_timeout. Mark downloading
                    # only after the fan-out kicks off, so a fan-out exception
                    # leaves the request ``approved`` and it retries next cycle.
                    await _try_auto_download_movie_id_impl(movie.id)
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloading(request.id)
                async with bg_movie_service() as movie_service:
                    is_downloaded = await movie_service.is_movie_downloaded(movie=movie)
                if is_downloaded:
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloaded(request.id)
                    log.info(f"Downloaded movie request: {request.title}")
                else:
                    log.info(f"Movie added but not yet downloaded: {request.title}")

            elif request.media_type.value == "show":
                from miramedia.shows.service import (
                    _try_auto_download_show_id_impl,
                )

                async with bg_show_service() as show_service:
                    show = await show_service.add_show(
                        external_id=effective_id,
                        metadata_provider=metadata_provider,
                    )
                if is_fresh:
                    await _try_auto_download_show_id_impl(show.id)
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloading(request.id)
                has_downloaded = False
                async with bg_show_service() as show_service:
                    for season in show.seasons:
                        for episode in season.episodes:
                            if await show_service.is_episode_downloaded(
                                episode=episode,
                                season=season,
                                show=show,
                            ):
                                has_downloaded = True
                                break
                        if has_downloaded:
                            break
                if has_downloaded:
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloaded(request.id)
                    log.info(f"Downloaded show request: {request.title}")
                else:
                    log.info(f"Show added but not yet downloaded: {request.title}")

        except Exception:
            log.exception(f"Failed to fulfill request: {request.title}")


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
    """Map of task_name -> cron expression for tasks whose cron is config-driven.

    Read fresh from the singleton on each call so it reflects current overrides.
    """
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
    # Subtitles / imports-scan / requests cron is kept synced UNCONDITIONALLY
    # (the tasks self-guard on enable state at run time). This way an interval
    # change made while the feature is off still applies the moment it's
    # re-enabled, with no restart.
    targets["miramedia.scheduler:scan_missing_subtitles_task"] = _interval_cron(
        cfg.subtitles.native.scan_interval_hours
    )
    targets["miramedia.scheduler:scheduled_library_scan_task"] = _interval_cron(
        cfg.imports.auto_scan_interval_hours
    )
    targets["miramedia.scheduler:fulfill_approved_requests_task"] = _interval_cron(
        cfg.requests.fulfill_interval_hours
    )
    # check_for_updates_task and verify_imported_files_task are also
    # always-registered, guard-inside tasks — keep their interval synced
    # unconditionally so edits apply without a restart, on or off.
    targets["miramedia.scheduler:check_for_updates_task"] = _interval_cron(
        cfg.updates.check_interval_hours
    )
    targets["miramedia.scheduler:verify_imported_files_task"] = _interval_cron(
        cfg.misc.integrity_check_interval_hours
    )
    return targets


async def refresh_dynamic_schedules() -> None:
    """Update the taskiq_schedulers rows in-place to match the current config.

    Called after a settings save so interval-driven cron expressions take effect
    without a process restart. Schedules that don't yet exist (e.g. the relevant
    feature was disabled at startup) are skipped — they will be seeded on next boot.
    """
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

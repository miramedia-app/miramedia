"""Lifespan startup phases extracted from main.py."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from taskiq.receiver import Receiver
from taskiq_fastapi import populate_dependency_context

from miramedia.config import MiraMediaConfig
from miramedia.database import init_engine
from miramedia.logging import attach_db_handler
from miramedia.scheduler import (
    background_broker,
    build_scheduler_loop,
    import_all_movie_torrents_task,
    import_all_show_torrents_task,
    interactive_broker,
    update_all_movies_metadata_task,
    update_all_shows_metadata_task,
)

if TYPE_CHECKING:
    from miramedia.torrents.backends.native import NativeDownloadClient

log = logging.getLogger(__name__)
config = MiraMediaConfig()

# Strong references to fire-and-forget startup tasks. asyncio holds only a
# weak reference to the task, so without this the task could be GC'd before
# it finishes. add_done_callback(discard) drops the reference once it's done.
_startup_tasks: set[asyncio.Task] = set()


@dataclass
class SchedulerContext:
    brokers_started: list = field(default_factory=list)
    started_sources: list = field(default_factory=list)
    interactive_finish: asyncio.Event | None = None
    background_finish: asyncio.Event | None = None
    interactive_receiver_task: asyncio.Task | None = None
    background_receiver_task: asyncio.Task | None = None
    startup_kick_task: asyncio.Task | None = None
    loop_task: asyncio.Task | None = None
    scheduler_leader_conn = None


def configure_threadpool() -> None:
    # Raise the anyio threadpool ceiling so sync FastAPI routes (which
    # block on psycopg DB calls) don't queue behind each other. Default is
    # ~40 — fine for a CRUD demo, too few when the dashboard fans out a
    # dozen queries in parallel against a busy library. Configurable via
    # MIRAMEDIA_THREADPOOL_SIZE for diagnosis.
    import anyio

    threadpool_size = int(os.getenv("MIRAMEDIA_THREADPOOL_SIZE", "200"))
    anyio.to_thread.current_default_thread_limiter().total_tokens = threadpool_size
    log.info("anyio threadpool size set to %d", threadpool_size)


async def start_persistence() -> bool:
    init_engine(config.database)
    attach_db_handler()

    event_bridge_started = False
    if os.getenv("MIRAMEDIA_EVENT_BRIDGE_DISABLED", "false").lower() != "true":
        try:
            from miramedia.database import render_db_url
            from miramedia.events.bus import get_event_bus

            await get_event_bus().start_postgres_bridge(
                render_db_url(
                    config.database.user,
                    config.database.password,
                    config.database.host,
                    config.database.port,
                    config.database.dbname,
                    driver="psycopg_plain",
                )
            )
            event_bridge_started = True
        except Exception:
            log.exception(
                "Failed to start Postgres event bridge; continuing local-only"
            )

    # Pre-warm the request pool so the first concurrent burst of requests
    # doesn't pay TLS+startup latency on cold connections. We open a small
    # number in parallel and immediately release them back to the pool.
    from miramedia.database import SessionLocalBackground, get_engine

    async def _warm_pool(eng: AsyncEngine, count: int) -> None:
        async def _ping() -> None:
            try:
                async with eng.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            except Exception:
                log.exception("pool warmup ping failed (non-fatal)")

        await asyncio.gather(*(_ping() for _ in range(count)))

    warm_target = min(int(os.getenv("MIRAMEDIA_DB_POOL_SIZE", "20")) // 2, 8)
    await _warm_pool(get_engine(), warm_target)

    # Coalesce all boot-time DB work into ONE background-pool session so we
    # don't open / close 5 separate request-pool connections sequentially.
    # Each step touches different tables but they don't interact, so order
    # is irrelevant.
    from miramedia.indexers.seed import seed_preloaded_sites
    from miramedia.movies.cleanup import cleanup_stale_movie_preferences
    from miramedia.settings.repository import SettingsRepository
    from miramedia.settings.service import apply_overrides_to_config
    from miramedia.shows.cleanup import cleanup_stale_show_preferences
    from miramedia.torrents.repository import TorrentRepository

    assert SessionLocalBackground is not None  # noqa: S101 — invariant guard
    async with SessionLocalBackground() as db:
        await seed_preloaded_sites(db)
        await TorrentRepository(db).delete_orphaned_torrents()
        overrides = await SettingsRepository(db).get_overrides()
        if overrides:
            apply_overrides_to_config(config, overrides)
            log.info(
                "Applied %d config override section(s) from database",
                len(overrides),
            )
        await cleanup_stale_show_preferences(db, config)
        await cleanup_stale_movie_preferences(db, config)
        await db.commit()

    from miramedia.auth.runtime import initialize_auth_runtime

    await initialize_auth_runtime()

    # config.misc.development is now final (config.toml + any DB override).
    # Force DEBUG end-to-end so the toggle actually surfaces debug logs —
    # otherwise the root logger stays at MIRAMEDIA_LOG_LEVEL (INFO) and drops
    # DEBUG records before any handler, including the DB log handler, sees them.
    from miramedia.logging import apply_development_log_level

    apply_development_log_level(config.misc.development)
    if config.misc.development:
        log.info("Development mode: log level forced to DEBUG")

    # Deprecated admin_emails → migrate any matching users to is_superuser=True at startup
    from miramedia.auth.users import (
        create_default_admin_user,
        migrate_admin_emails_to_superuser_flag,
    )

    try:
        await migrate_admin_emails_to_superuser_flag()
    except Exception:
        log.exception("Failed to migrate admin_emails to user.is_superuser")

    await create_default_admin_user()

    return event_bridge_started


def start_library_watcher() -> None:
    if os.getenv("MIRAMEDIA_LIBRARY_WATCHER", "false").lower() == "true":
        from miramedia.library_watcher import run_library_watcher

        _watcher_task = asyncio.create_task(run_library_watcher())
        _startup_tasks.add(_watcher_task)
        _watcher_task.add_done_callback(_startup_tasks.discard)


def schedule_import_queue_warmup() -> None:
    async def _warm_import_queue() -> None:
        await asyncio.sleep(10)
        try:
            from miramedia.database import (
                SessionLocalBackground,
                bg_movie_service,
                bg_show_service,
                bg_torrent_service,
            )
            from miramedia.imports.queue.sync import rebuild_import_queue
            from miramedia.imports.repository import ImportsRepository
            from miramedia.imports.service import ImportsService

            assert SessionLocalBackground is not None  # noqa: S101 — invariant guard
            async with SessionLocalBackground() as db:
                async with bg_torrent_service() as torrent_service:
                    async with bg_show_service() as show_service:
                        async with bg_movie_service() as movie_service:
                            service = ImportsService(
                                repository=ImportsRepository(db),
                                torrent_service=torrent_service,
                                show_service=show_service,
                                movie_service=movie_service,
                            )
                            # In-process workers (Receiver) cannot survive this
                            # restart, so any leftover "queued" scan row is an
                            # orphan whose import never finished — reclaim it
                            # before rebuilding so it shows as retryable, not
                            # stuck "Importing".
                            reclaimed = (
                                await service.repository.reclaim_stale_queued_imports()
                            )
                            if reclaimed:
                                log.info(
                                    "Reclaimed %d orphaned queued import(s) on startup",
                                    reclaimed,
                                )
                            await rebuild_import_queue(db, service)
        except Exception:
            log.exception("Import queue warm-up failed (non-fatal)")

    _warm_queue_task = asyncio.create_task(_warm_import_queue())
    _startup_tasks.add(_warm_queue_task)
    _warm_queue_task.add_done_callback(_startup_tasks.discard)


def is_scheduler_disabled() -> bool:
    # With uvicorn --workers N this lifespan runs once per child process.
    # Set MIRAMEDIA_SCHEDULER_DISABLED=true on API workers when a dedicated
    # scheduler container owns the broker; otherwise every worker spawns
    # its own Receiver + cron loop and scheduled tasks fire N times. The
    # line below appears once per worker so duplicate-spawn regressions
    # surface in logs.
    scheduler_disabled = (
        os.getenv("MIRAMEDIA_SCHEDULER_DISABLED", "false").lower() == "true"
    )
    log.info(
        "MIRAMEDIA_WEB_WORKERS=%s, scheduler %s",
        os.getenv("MIRAMEDIA_WEB_WORKERS", "1"),
        "DISABLED" if scheduler_disabled else "enabled",
    )
    return scheduler_disabled


async def start_native_torrent_client() -> NativeDownloadClient | None:
    # Initialize native torrent client if enabled
    native_client = None
    if config.torrents.native.enabled:
        from miramedia.torrents.backends.native import NativeDownloadClient

        try:
            native_client = NativeDownloadClient()
            log.info("Native torrent client started")
            # Catch up on torrents that were imported via paths that don't
            # trigger cleanup_after_import (manual map, auto-import-on-scan,
            # legacy entries) by sweeping stale ``.fastresume`` files.
            try:
                await native_client.reconcile_resume_data()
            except Exception:
                log.exception("Failed to reconcile native resume data on startup")
        except Exception:
            log.exception("Failed to start native torrent client")
    return native_client


def warm_cloudflare_bypass() -> None:
    # Pre-warm chromium on the cf-bypass worker loop so the first
    # user-triggered indexer query doesn't pay the 5-8s cold-start tax.
    # Fire-and-forget — the future runs on the dedicated worker loop and we
    # don't block boot on chromium readiness.
    try:
        from miramedia.cloudflare import get_cloudflare_bypass

        cf_cfg = MiraMediaConfig().cloudflare
        if not cf_cfg.enabled:
            log.info("Cloudflare bypass disabled; skipping warmup")
        elif cf_cfg.warmup_on_startup:
            get_cloudflare_bypass().warm(timeout=cf_cfg.browser_launch_timeout_seconds)
            log.info(
                "Cloudflare bypass warmup scheduled (timeout=%.0fs)",
                cf_cfg.browser_launch_timeout_seconds,
            )
    except Exception:
        log.exception("Failed to schedule Cloudflare bypass warmup")


async def acquire_scheduler_leadership() -> tuple[object | None, bool]:
    from miramedia.database import get_engine

    scheduler_leader_conn = await get_engine().connect()
    try:
        is_scheduler_leader = bool(
            await scheduler_leader_conn.scalar(
                text("SELECT pg_try_advisory_lock(4871260042)")
            )
        )
    except BaseException:
        await scheduler_leader_conn.close()
        raise
    return scheduler_leader_conn, is_scheduler_leader


async def start_scheduler_workers(app: FastAPI, ctx: SchedulerContext) -> None:
    log.info("Acquired scheduler advisory lock")

    # Two-lane priority isolation: separate brokers (different NOTIFY
    # channels + ``taskiq_messages_*`` tables) so a long background
    # sweep can't starve user-triggered actions. See scheduler.py for
    # the rationale on why two brokers (not two receivers on one
    # broker) — short version: PostgresqlBroker.listen() atomically
    # claims rows via DELETE RETURNING, so receivers on the same
    # broker would race + steal each other's messages.
    for b in (interactive_broker, background_broker):
        if not b.is_worker_process:
            await b.startup()
            ctx.brokers_started.append(b)
        populate_dependency_context(b, app)
    scheduler_loop = build_scheduler_loop()
    for source in scheduler_loop.scheduler.sources:
        await source.startup()
        ctx.started_sources.append(source)

    # Lane budgets. Resolution order:
    #   1. explicit lane env vars take precedence
    #   2. legacy MIRAMEDIA_RECEIVER_MAX_TASKS becomes the SUM cap
    #      and is auto-split 80/20 interactive/background
    #   3. fallback defaults (8 interactive + 2 background)
    interactive_env = os.getenv("MIRAMEDIA_INTERACTIVE_TASK_LIMIT")
    background_env = os.getenv("MIRAMEDIA_BACKGROUND_TASK_LIMIT")
    legacy_total = os.getenv("MIRAMEDIA_RECEIVER_MAX_TASKS")
    if interactive_env is not None or background_env is not None:
        interactive_max = max(1, int(interactive_env or "8"))
        background_max = max(1, int(background_env or "2"))
    elif legacy_total is not None:
        total = max(2, int(legacy_total))
        interactive_max = max(1, round(total * 0.8))
        background_max = max(1, total - interactive_max)
    else:
        interactive_max = 8
        background_max = 2

    interactive_receiver = Receiver(
        interactive_broker,
        run_startup=False,
        max_async_tasks=interactive_max,
    )
    background_receiver = Receiver(
        background_broker,
        run_startup=False,
        max_async_tasks=background_max,
    )
    log.info(
        "TaskIQ lanes: interactive=%d background=%d (total=%d)",
        interactive_max,
        background_max,
        interactive_max + background_max,
    )
    ctx.interactive_finish = asyncio.Event()
    ctx.background_finish = asyncio.Event()
    ctx.interactive_receiver_task = asyncio.create_task(
        interactive_receiver.listen(ctx.interactive_finish)
    )
    ctx.background_receiver_task = asyncio.create_task(
        background_receiver.listen(ctx.background_finish)
    )
    ctx.loop_task = asyncio.create_task(scheduler_loop.run(skip_first_run=True))

    # Defer the bulk-startup workload (imports, metadata refresh,
    # chained auto-download) by 30s so the app is up + responsive
    # before the receiver fans out heavy work. Without this delay all
    # four tasks kick off the moment uvicorn binds, and the first
    # human page-load competes with libtorrent resume init + chromium
    # warmup + metadata HTTP storms.
    async def _kick_startup_tasks() -> None:
        try:
            await asyncio.sleep(30)
            # Indexer tasks (import_all_*_torrents) need the
            # Cloudflare bypass browser for 1337x/etc. Wait briefly
            # so they don't all queue on the launch lock at once.
            # Metadata tasks don't need bypass but the cost of
            # waiting alongside them is negligible.
            try:
                from miramedia.cloudflare import get_cloudflare_bypass

                cf_cfg = MiraMediaConfig().cloudflare
                if cf_cfg.enabled and cf_cfg.warmup_on_startup:
                    ready = await get_cloudflare_bypass().await_ready(
                        timeout=cf_cfg.browser_launch_timeout_seconds
                    )
                    if ready:
                        log.info("Cloudflare bypass ready; kicking startup tasks")
                    else:
                        log.warning(
                            "Cloudflare bypass not ready within %.0fs; "
                            "kicking startup tasks anyway (will lazy-launch)",
                            cf_cfg.browser_launch_timeout_seconds,
                        )
            except Exception:
                log.exception("await_ready failed; kicking startup tasks anyway")
            # Stagger startup tasks so they don't pile onto the same
            # minute-cron tick and compete for CF bypass + DB pool.
            await import_all_movie_torrents_task.kiq()
            await asyncio.sleep(8)
            await import_all_show_torrents_task.kiq()
            await asyncio.sleep(8)
            await update_all_movies_metadata_task.kiq()
            await asyncio.sleep(8)
            await update_all_shows_metadata_task.kiq()
        except Exception:
            log.exception("Failed to submit initial background tasks during startup.")

    ctx.startup_kick_task = asyncio.create_task(_kick_startup_tasks())


async def shutdown_startup(
    ctx: SchedulerContext,
    native_client: NativeDownloadClient | None,
    event_bridge_started: bool,
) -> None:
    # All scheduler-related vars stay None when scheduler_disabled is
    # true, so these branches no-op naturally on API-only workers.
    if ctx.startup_kick_task is not None and not ctx.startup_kick_task.done():
        ctx.startup_kick_task.cancel()
        try:
            await ctx.startup_kick_task
        except asyncio.CancelledError:
            pass
    if ctx.loop_task is not None and not ctx.loop_task.done():
        ctx.loop_task.cancel()
        try:
            await ctx.loop_task
        except asyncio.CancelledError:
            pass
    # Cancel fire-and-forget startup tasks (library watcher = infinite loop,
    # import-queue warm-up) so they don't run against a torn-down engine on a
    # non-exit lifespan teardown (tests, --reload, embedding).
    outstanding = [t for t in list(_startup_tasks) if not t.done()]
    for t in outstanding:
        t.cancel()
    if outstanding:
        await asyncio.gather(*outstanding, return_exceptions=True)
    # Signal both receivers to drain in parallel, then wait for both.
    pending_receivers: list[asyncio.Task] = []
    if ctx.interactive_finish is not None and ctx.interactive_receiver_task is not None:
        ctx.interactive_finish.set()
        pending_receivers.append(ctx.interactive_receiver_task)
    if ctx.background_finish is not None and ctx.background_receiver_task is not None:
        ctx.background_finish.set()
        pending_receivers.append(ctx.background_receiver_task)
    if pending_receivers:
        await asyncio.gather(*pending_receivers, return_exceptions=True)
    for source in ctx.started_sources:
        await source.shutdown()
    for b in ctx.brokers_started:
        await b.shutdown()
    if native_client is not None:
        # shutdown() pauses the session + polls the alert queue with
        # time.sleep for up to ~10s to flush resume data. Run it off-loop so
        # it doesn't block the parallel broker/source shutdown + CF reaper
        # (the cron resume-save already uses to_thread for the same reason).
        await asyncio.to_thread(native_client.shutdown)
    # Stop the cloudflare-bypass worker loop + reap chromium so the
    # container exits cleanly instead of leaving the daemon thread
    # dangling.
    try:
        from miramedia.cloudflare import get_cloudflare_bypass

        bypass = get_cloudflare_bypass()
        if bypass is not None:
            bypass.shutdown()
    except Exception:
        log.exception("Cloudflare bypass shutdown failed")
    # Release pooled HTTP connections (httpx + IPv4-pinned requests
    # session) so the process exits without leaking sockets / file
    # descriptors on container restarts.
    try:
        from miramedia.indexers.sites.base import close_http_client

        close_http_client()
    except Exception:
        log.exception("httpx client close failed (non-fatal)")
    try:
        from miramedia.metadata.backends.native import close_ipv4_session

        close_ipv4_session()
    except Exception:
        log.exception("ipv4 session close failed (non-fatal)")
    if ctx.scheduler_leader_conn is not None:
        try:
            await ctx.scheduler_leader_conn.execute(
                text("SELECT pg_advisory_unlock(4871260042)")
            )
        except Exception:
            log.exception("Failed to release scheduler advisory lock")
        try:
            await ctx.scheduler_leader_conn.close()
        except Exception:  # noqa: S110 — best-effort cleanup, non-fatal
            pass
    if event_bridge_started:
        try:
            from miramedia.events.bus import get_event_bus

            await get_event_bus().stop_postgres_bridge()
        except Exception:
            log.exception("Postgres event bridge shutdown failed")

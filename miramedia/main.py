import asyncio
import logging
import mimetypes
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import anyio
import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.datastructures import Headers
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import FileResponse, RedirectResponse
from starlette.routing import Match, Mount
from starlette.staticfiles import NotModifiedResponse
from starlette.types import Scope
from taskiq.receiver import Receiver
from taskiq_fastapi import populate_dependency_context
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import miramedia.imports.router as imports_router
import miramedia.movies.router as movies_router
import miramedia.shows.router as shows_router
import miramedia.torrents.router as torrents_router
from miramedia.auth.router import (
    auth_metadata_router,
    get_openid_router,
)
from miramedia.auth.router import (
    users_router as custom_users_router,
)
from miramedia.auth.schemas import UserCreate, UserRead, UserUpdate
from miramedia.auth.users import (
    bearer_auth_backend,
    cookie_auth_backend,
    current_superuser,
    fastapi_users,
)
from miramedia.config import MiraMediaConfig
from miramedia.database import DbSessionDependency, init_engine
from miramedia.exceptions import register_exception_handlers
from miramedia.filesystem_checks import run_filesystem_checks
from miramedia.logging import LOGGING_CONFIG, attach_db_handler, setup_logging
from miramedia.notifications.router import router as notification_router
from miramedia.scheduler import (
    background_broker,
    build_scheduler_loop,
    import_all_movie_torrents_task,
    import_all_show_torrents_task,
    interactive_broker,
    update_all_movies_metadata_task,
    update_all_shows_metadata_task,
)

setup_logging()

config = MiraMediaConfig()
log = logging.getLogger(__name__)

# Strong references to fire-and-forget startup tasks. asyncio holds only a
# weak reference to the task, so without this the task could be GC'd before
# it finishes. add_done_callback(discard) drops the reference once it's done.
_startup_tasks: set[asyncio.Task] = set()


if config.misc.development:
    log.warning("Development Mode activated!")

run_filesystem_checks(config, log)

BASE_PATH = os.getenv("BASE_PATH", "")
FRONTEND_FILES_DIR = os.getenv("FRONTEND_FILES_DIR")
DISABLE_FRONTEND_MOUNT = (
    os.getenv("MIRAMEDIA_DISABLE_FRONTEND_MOUNT", "").lower() == "true"
)
FRONTEND_FOLLOW_SYMLINKS = os.getenv("FRONTEND_FOLLOW_SYMLINKS", "").lower() == "true"

log.info("Hello World!")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    # Raise the anyio threadpool ceiling so sync FastAPI routes (which
    # block on psycopg DB calls) don't queue behind each other. Default is
    # ~40 — fine for a CRUD demo, too few when the dashboard fans out a
    # dozen queries in parallel against a busy library. Configurable via
    # MIRAMEDIA_THREADPOOL_SIZE for diagnosis.
    import anyio

    threadpool_size = int(os.getenv("MIRAMEDIA_THREADPOOL_SIZE", "200"))
    anyio.to_thread.current_default_thread_limiter().total_tokens = threadpool_size
    log.info("anyio threadpool size set to %d", threadpool_size)

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

    if os.getenv("MIRAMEDIA_LIBRARY_WATCHER", "false").lower() == "true":
        from miramedia.library_watcher import run_library_watcher

        _watcher_task = asyncio.create_task(run_library_watcher())
        _startup_tasks.add(_watcher_task)
        _watcher_task.add_done_callback(_startup_tasks.discard)

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

    brokers_started: list = []
    started_sources: list = []
    interactive_finish: asyncio.Event | None = None
    background_finish: asyncio.Event | None = None
    interactive_receiver_task: asyncio.Task | None = None
    background_receiver_task: asyncio.Task | None = None
    startup_kick_task: asyncio.Task | None = None
    loop_task: asyncio.Task | None = None
    native_client = None
    scheduler_leader_conn = None

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

    # Initialize native torrent client if enabled
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

    # Pre-warm chromium on the cf-bypass worker loop so the first
    # user-triggered indexer query doesn't pay the 5-8s cold-start tax.
    # Fire-and-forget — the future runs on the dedicated worker loop and we
    # don't block boot on chromium readiness.
    try:
        from miramedia.cloudflare import get_cloudflare_bypass
        from miramedia.config import MiraMediaConfig

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

    try:
        if scheduler_disabled:
            log.info(
                "MIRAMEDIA_SCHEDULER_DISABLED=true — taskiq brokers, receivers, "
                "and scheduler loop skipped on this worker"
            )
            yield
        else:
            from miramedia.database import get_engine

            scheduler_leader_conn = await get_engine().connect()
            is_scheduler_leader = bool(
                await scheduler_leader_conn.scalar(
                    text("SELECT pg_try_advisory_lock(4871260042)")
                )
            )
            if not is_scheduler_leader:
                log.info(
                    "Another worker owns the scheduler advisory lock; "
                    "serving API only in this worker"
                )
                yield
                return
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
                    brokers_started.append(b)
                populate_dependency_context(b, app)
            scheduler_loop = build_scheduler_loop()
            for source in scheduler_loop.scheduler.sources:
                await source.startup()
                started_sources.append(source)

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
            interactive_finish = asyncio.Event()
            background_finish = asyncio.Event()
            interactive_receiver_task = asyncio.create_task(
                interactive_receiver.listen(interactive_finish)
            )
            background_receiver_task = asyncio.create_task(
                background_receiver.listen(background_finish)
            )
            loop_task = asyncio.create_task(scheduler_loop.run(skip_first_run=True))

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
                        from miramedia.config import MiraMediaConfig

                        cf_cfg = MiraMediaConfig().cloudflare
                        if cf_cfg.enabled and cf_cfg.warmup_on_startup:
                            ready = await get_cloudflare_bypass().await_ready(
                                timeout=cf_cfg.browser_launch_timeout_seconds
                            )
                            if ready:
                                log.info(
                                    "Cloudflare bypass ready; kicking startup tasks"
                                )
                            else:
                                log.warning(
                                    "Cloudflare bypass not ready within %.0fs; "
                                    "kicking startup tasks anyway (will lazy-launch)",
                                    cf_cfg.browser_launch_timeout_seconds,
                                )
                    except Exception:
                        log.exception(
                            "await_ready failed; kicking startup tasks anyway"
                        )
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
                    log.exception(
                        "Failed to submit initial background tasks during startup."
                    )

            startup_kick_task = asyncio.create_task(_kick_startup_tasks())
            yield
    finally:
        # All scheduler-related vars stay None when scheduler_disabled is
        # true, so these branches no-op naturally on API-only workers.
        if startup_kick_task is not None and not startup_kick_task.done():
            startup_kick_task.cancel()
            try:
                await startup_kick_task
            except asyncio.CancelledError:
                pass
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
        # Signal both receivers to drain in parallel, then wait for both.
        pending_receivers: list[asyncio.Task] = []
        if interactive_finish is not None and interactive_receiver_task is not None:
            interactive_finish.set()
            pending_receivers.append(interactive_receiver_task)
        if background_finish is not None and background_receiver_task is not None:
            background_finish.set()
            pending_receivers.append(background_receiver_task)
        if pending_receivers:
            await asyncio.gather(*pending_receivers, return_exceptions=True)
        for source in started_sources:
            await source.shutdown()
        for b in brokers_started:
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
        if scheduler_leader_conn is not None:
            try:
                await scheduler_leader_conn.execute(
                    text("SELECT pg_advisory_unlock(4871260042)")
                )
            except Exception:
                log.exception("Failed to release scheduler advisory lock")
            try:
                await scheduler_leader_conn.close()
            except Exception:  # noqa: S110 — best-effort cleanup, non-fatal
                pass
        if event_bridge_started:
            try:
                from miramedia.events.bus import get_event_bus

                await get_event_bus().stop_postgres_bridge()
            except Exception:
                log.exception("Postgres event bridge shutdown failed")


# Swagger UI / ReDoc are replaced by the embedded Scalar API reference in the
# docs site (frontend /docs/api-reference). The backend only exposes the raw
# OpenAPI schema at /openapi.json.
app = FastAPI(
    root_path=BASE_PATH,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.middleware("http")
async def server_timing_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
    return response


@app.middleware("http")
async def api_trailing_slash_redirect_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Make the trailing-slash variant of API collection-root endpoints resolve.

    Collection roots are declared ``@router.get("")`` so the canonical path is
    the *no-slash* form (e.g. ``GET /api/v1/shows``); the OpenAPI spec and the
    generated frontend client both use that form. Starlette ships
    ``redirect_slashes=True`` which would normally 307 ``/api/v1/shows/`` →
    ``/api/v1/shows``, but that fallback only runs when *no* route matches the
    incoming path. The SPA is served from a catch-all ``app.mount("/")``: a
    ``Mount`` at ``/`` returns ``Match.FULL`` for *every* path, so the
    trailing-slash request is handed straight to ``StaticFiles`` (which 404s on
    the missing file) before Starlette ever reaches its slash-redirect logic.

    Rather than touch dozens of route decorators (which would change the OpenAPI
    paths / the generated ``api.d.ts``), we restore the missing behaviour in one
    place: for any ``/api/`` path ending in ``/``, strip the slash and, *only if*
    the stripped path matches a real registered route, emit a 307 to the
    canonical no-slash form. Paths that don't map to a route (genuine 404s,
    unknown API paths) fall through untouched, so existing 404 behaviour and the
    SPA fallback are unaffected.
    """
    path = request.url.path
    if (
        path.startswith("/api/")
        and path.endswith("/")
        and path != "/api/"
        and path != "/api//"
    ):
        stripped = path.rstrip("/")
        # Probe the registered routes against the stripped path using a shallow
        # scope copy. Only redirect when something actually handles the no-slash
        # form, so genuine unknown paths keep 404ing.
        probe_scope = dict(request.scope)
        probe_scope["path"] = stripped
        for route in app.router.routes:
            match, _ = route.matches(probe_scope)
            # A bare ``Mount("/")`` matches everything; skip it so we only react
            # to real API routes, not the SPA catch-all.
            if match != Match.NONE and not (
                isinstance(route, Mount) and route.path == ""
            ):
                target = stripped
                if request.url.query:
                    target = f"{target}?{request.url.query}"
                return RedirectResponse(url=target, status_code=307)
    return await call_next(request)


# Middleware order: Starlette evaluates the LAST `add_middleware` call as the
# innermost layer (closest to the app), and the FIRST call as the outermost.
# Effective request flow (outermost → innermost):
#   1. ProxyHeadersMiddleware — needs the raw client IP from forwarded headers
#      before anything else touches the scope.
#   2. CORSMiddleware — answer preflight OPTIONS before correlation tagging,
#      otherwise short-circuited preflights skip our id header anyway.
#   3. GZipMiddleware — compress responses (HTML, JSON, JS, CSS, fonts). The
#      ``minimum_size=1000`` threshold skips small payloads where compression
#      adds CPU + framing overhead but no meaningful size win.
#   4. CorrelationIdMiddleware — innermost so every response (including
#      handler-raised errors) carries an X-Correlation-ID.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
origins = config.misc.cors_urls
log.info(f"CORS URLs activated for following origins: {origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    # Without this, cross-origin JS cannot read X-Total-Count and paginated
    # lists collapse to a single page.
    expose_headers=["X-Total-Count"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CorrelationIdMiddleware, header_name="X-Correlation-ID")
api_app = APIRouter(prefix="/api/v1")


_EXPECTED_ALEMBIC_HEAD: str | None = None


def _get_expected_alembic_head() -> str | None:
    global _EXPECTED_ALEMBIC_HEAD
    if _EXPECTED_ALEMBIC_HEAD is None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config("alembic.ini")
        _EXPECTED_ALEMBIC_HEAD = ScriptDirectory.from_config(cfg).get_current_head()
    return _EXPECTED_ALEMBIC_HEAD


@api_app.get("/health")
async def hello_world() -> dict:
    """Healthcheck must never raise — partial degradation is reported via
    per-section ``.ok`` flags so the docker compose healthcheck does not flap
    when an optional subsystem (cache, pool) is temporarily unavailable.

    DB pings use a dedicated ``NullPool`` engine so a saturated request /
    background pool cannot stall the liveness probe. Both real pools' stats
    are reported. An ``alembic`` section flags head-mismatch (deployed app
    expects revision X but DB is at Y) which would otherwise silently
    surface as ``UndefinedColumn`` errors on the first hot path.
    """
    payload: dict = {
        "status": "ok",
        "version": os.getenv("PUBLIC_VERSION"),
        "message": "Hello World!",
    }

    db_section: dict = {"ok": False}
    try:
        from miramedia.database import (
            background_engine,
            export_pool_gauges,
            get_engine,
            healthcheck_engine,
        )

        if healthcheck_engine is None:
            db_section["error"] = "healthcheck engine not initialised"
        else:
            try:
                async with healthcheck_engine.connect() as conn:
                    await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=2.0)
                db_section["ok"] = True
            except TimeoutError:
                db_section["error"] = "timeout"

        pools: dict[str, dict] = {}
        for name, eng in (("request", get_engine()), ("background", background_engine)):
            if eng is None:
                continue
            try:
                p = eng.pool
                pools[name] = {
                    "size": p.size(),
                    "checked_out": p.checkedout(),
                    "overflow": p.overflow(),
                }
            except Exception as exc:
                pools[name] = {"error": str(exc)}
        db_section["pools"] = pools

        # Refresh pool gauges for the /metrics endpoint while we're here —
        # cheap, idempotent, and means consumers see fresh values without
        # an extra scrape-hook hookup.
        try:
            export_pool_gauges()
        except Exception:  # noqa: S110 — best-effort gauge refresh, non-fatal
            pass
    except Exception as exc:
        db_section = {"ok": False, "error": str(exc)}
    payload["db"] = db_section

    # Alembic head vs DB head — divergence means the deployed image expects a
    # schema the DB doesn't have. Expected head is memoized (filesystem scan is
    # immutable for the process). Current revision is read with a raw SELECT to
    # avoid MigrationContext.configure(), which logs at INFO on every call.
    alembic_section: dict = {"ok": False}
    try:
        from miramedia.database import healthcheck_engine as _hc

        expected = _get_expected_alembic_head()
        async with _hc.connect() as conn:  # type: ignore[union-attr]
            row = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).first()
        current = row[0] if row else None
        alembic_section = {
            "ok": current == expected,
            "expected_head": expected,
            "current_revision": current,
        }
    except Exception as exc:
        alembic_section = {"ok": False, "error": str(exc)}
    payload["alembic"] = alembic_section

    # Metadata cache stats — uses get_all_cache_stats() from the cache module.
    cache_section: dict = {"ok": False}
    try:
        from miramedia.metadata.cache import get_all_cache_stats

        stats = get_all_cache_stats()
        sum_hits = sum(s["hits"] for s in stats.values())
        sum_misses = sum(s["misses"] for s in stats.values())
        total = sum_hits + sum_misses
        cache_section = {
            "ok": True,
            "metadata": {
                "sum_hits": sum_hits,
                "sum_misses": sum_misses,
                "hit_rate": round(sum_hits / total, 4) if total > 0 else 0.0,
                "cache_count": len(stats),
                "per_cache": stats,
            },
        }
    except Exception as exc:
        cache_section = {"ok": False, "error": str(exc)}
    payload["cache"] = cache_section

    return payload


class FeatureFlags(BaseModel):
    requests: bool
    subtitles: bool
    notifications: bool


@api_app.get("/features")
async def get_features() -> FeatureFlags:
    return FeatureFlags(
        requests=config.requests.enabled,
        subtitles=config.subtitles.enabled,
        notifications=config.notifications.native.enabled,
    )


class DashboardSummary(BaseModel):
    shows: int = 0
    movies: int = 0
    torrents: int = 0
    requests_pending: int = 0
    imports_failed: int = 0
    imports_ambiguous: int = 0


@api_app.get("/dashboard/summary")
async def get_dashboard_summary(db: DbSessionDependency) -> DashboardSummary:
    """One cheap dashboard-count read instead of several parallel requests."""
    from miramedia.file_status import ImportOutcome
    from miramedia.movies.models import Movie, MovieFile
    from miramedia.requests.models import MediaRequest, RequestStatus
    from miramedia.shows.models import EpisodeFile, Show
    from miramedia.torrents.models import Torrent
    from miramedia.torrents.schemas import TorrentStatus

    failed_statuses = (ImportOutcome.failed_io, ImportOutcome.failed_no_match)
    show_count = await db.scalar(select(func.count()).select_from(Show))
    movie_count = await db.scalar(select(func.count()).select_from(Movie))
    torrent_count = await db.scalar(
        select(func.count())
        .select_from(Torrent)
        .where(Torrent.status != TorrentStatus.finished)
    )
    pending_requests = await db.scalar(
        select(func.count())
        .select_from(MediaRequest)
        .where(MediaRequest.status == RequestStatus.pending)
    )
    # Collapse the failed/ambiguous counts per table into a single SELECT each
    # using conditional aggregates, so we issue one round-trip per table instead
    # of four.
    movie_failed, movie_ambiguous = (
        await db.execute(
            select(
                func.count().filter(MovieFile.import_status.in_(failed_statuses)),
                func.count().filter(MovieFile.import_status == ImportOutcome.ambiguous),
            ).select_from(MovieFile)
        )
    ).one()
    episode_failed, episode_ambiguous = (
        await db.execute(
            select(
                func.count().filter(EpisodeFile.import_status.in_(failed_statuses)),
                func.count().filter(
                    EpisodeFile.import_status == ImportOutcome.ambiguous
                ),
            ).select_from(EpisodeFile)
        )
    ).one()
    return DashboardSummary(
        shows=int(show_count or 0),
        movies=int(movie_count or 0),
        torrents=int(torrent_count or 0),
        requests_pending=int(pending_requests or 0) if config.requests.enabled else 0,
        imports_failed=int(movie_failed or 0) + int(episode_failed or 0),
        imports_ambiguous=int(movie_ambiguous or 0) + int(episode_ambiguous or 0),
    )


api_app.include_router(
    fastapi_users.get_auth_router(bearer_auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
api_app.include_router(
    fastapi_users.get_auth_router(cookie_auth_backend),
    prefix="/auth/cookie",
    tags=["auth"],
)
api_app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
api_app.include_router(
    fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"]
)
api_app.include_router(
    fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"]
)
api_app.include_router(custom_users_router)
api_app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)
api_app.include_router(auth_metadata_router)

if get_openid_router():
    api_app.include_router(get_openid_router(), tags=["openid"], prefix="/auth/oauth")

api_app.include_router(shows_router.router)
api_app.include_router(shows_router.episodes_router)
api_app.include_router(shows_router.seasons_router)
api_app.include_router(torrents_router.router)
api_app.include_router(movies_router.router)
api_app.include_router(imports_router.router)
api_app.include_router(notification_router)

# SSE event stream — server-push channel that replaces dashboard polling
# loops on /dashboard/torrents and /dashboard/imports. Clients receive
# small ``{id}`` notifications and re-fetch via the REST endpoints they
# already use, so this stays additive to the existing API surface.
# The router imports below are deferred to avoid circular import / startup
# ordering issues — hence the E402 suppressions on each.
from miramedia.events.router import router as events_router  # noqa: E402

api_app.include_router(events_router)

from miramedia.indexers.router import router as indexers_router  # noqa: E402
from miramedia.logs.router import router as logs_router  # noqa: E402
from miramedia.settings.router import router as settings_router  # noqa: E402
from miramedia.updates.router import router as updates_router  # noqa: E402

api_app.include_router(indexers_router)
api_app.include_router(logs_router)
api_app.include_router(settings_router)
api_app.include_router(updates_router)

from miramedia.streams.router import router as streams_router  # noqa: E402
from miramedia.subtitles.router import router as subtitles_router  # noqa: E402

api_app.include_router(streams_router)
api_app.include_router(subtitles_router)

# Mount requests router unconditionally so types appear in the OpenAPI
# spec; the router itself enforces the runtime ``requests.enabled`` flag
# via ``require_requests_enabled``. (import deferred: circular-import / ordering)
from miramedia.requests.router import router as requests_router  # noqa: E402

api_app.include_router(requests_router)
if config.requests.enabled:
    log.info("Requests feature enabled")

# Web Vitals beacon endpoint (POST /api/v1/analytics/vitals). Hidden from
# the OpenAPI schema by the router itself — it's an operational sink for
# the frontend, not a public API. The full Prometheus surface is exposed
# at /api/v1/metrics below. (import deferred: circular-import / ordering)
from miramedia.observability.router import router as observability_router  # noqa: E402

api_app.include_router(observability_router)
from miramedia.ops.router import router as ops_router  # noqa: E402

api_app.include_router(ops_router)

# Prometheus metrics. The instrumentator hooks into the parent ``app``
# (it needs the live ASGI middleware stack to time requests) and exposes
# the scrape endpoint on the same ``app`` — its ``.expose()`` helper calls
# ``add_route(path, route, include_in_schema=...)`` which only works on
# ``FastAPI``/``Starlette`` apps, not an ``APIRouter``. We still want the
# scrape URL under the ``/api/v1`` prefix so reverse-proxy ACLs that gate
# the API path also gate metrics, so the path is hard-coded here.
# Healthcheck + SSE stream are excluded — they're hit every second and
# would dominate the histogram with noise. include_in_schema=False keeps
# /metrics out of the OpenAPI spec (and therefore the Scalar docs).
# Default: deny — only superusers may scrape. Set misc.metrics_public=true
# to allow unauthenticated access (useful when Prometheus has no credentials
# and the endpoint is firewalled from end-users).
_metrics_kwargs: dict[str, Any] = {}
if not config.misc.metrics_public:
    _metrics_kwargs["dependencies"] = [Depends(current_superuser)]
Instrumentator(
    excluded_handlers=["/api/v1/health", "/api/v1/metrics", "/api/v1/events/stream"],
    should_group_status_codes=True,
).instrument(app).expose(
    app, endpoint="/api/v1/metrics", include_in_schema=False, **_metrics_kwargs
)

# Poster filenames are content-hashed (id-based), so a long cache + revalidate
# on metadata refresh is safe. Browsers can serve repeat hits from disk.


@app.get("/api/v1/static/image/{filename}")
def serve_image(
    filename: str,
    w: Annotated[int | None, Query(ge=64, le=1200)] = None,
) -> FileResponse:
    file_path = config.misc.image_directory / filename
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )
    if w is not None:
        variant = _poster_variant(file_path, w)
        if variant is not None:
            file_path = variant
    media_type = "image/jpeg"
    if filename.endswith(".avif"):
        media_type = "image/avif"
    elif filename.endswith(".webp"):
        media_type = "image/webp"
    elif filename.endswith(".png"):
        media_type = "image/png"
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _poster_variant(file_path: Path, width: int) -> Path | None:
    """Generate/cache a resized poster variant next to the image cache."""
    # TODO: this runs sync PIL on the event loop and the .variants cache dir
    # grows unbounded (no eviction). Consider offloading to a thread + adding a
    # cleanup/retention task.
    try:
        from PIL import Image

        width = min(
            (200, 300, 400, 600, 800), key=lambda candidate: abs(candidate - width)
        )
        variant_dir = config.misc.image_directory / ".variants"
        variant_dir.mkdir(parents=True, exist_ok=True)
        variant = variant_dir / f"{file_path.stem}-{width}{file_path.suffix}"
        if (
            variant.exists()
            and variant.stat().st_mtime_ns >= file_path.stat().st_mtime_ns
        ):
            return variant
        with Image.open(file_path) as image:
            image.thumbnail((width, int(width * 1.5)))
            save_kwargs = {"quality": 82, "optimize": True}
            if file_path.suffix.lower() in {".jpg", ".jpeg"}:
                save_kwargs["progressive"] = True
            image.save(variant, **save_kwargs)
    except Exception:
        log.debug("poster resize failed for %s", file_path, exc_info=True)
        return None
    else:
        return variant


app.include_router(api_app)


# Serve the SPA shell for unmatched frontend routes. Next.js static export
# emits per-route HTML files at known paths; dynamic routes (UUID segments)
# emit a single `_shell/index.html` that the client router resolves via
# `useParams()`. The 404 handler rewrites UUID paths to that shell so the
# backend doesn't have to know about every show/movie ID.
import re  # noqa: E402 — deferred to avoid circular import / startup ordering

_UUID_RE = re.compile(
    r"^/dashboard/(shows|movies)/[0-9a-fA-F-]{8,}(?:/[0-9a-fA-F-]{8,})?/?$"
)


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception) -> Response:
    if DISABLE_FRONTEND_MOUNT:
        return Response(content="Not Found", status_code=404)
    path = request.url.path
    # Rewrite UUID detail paths to the dynamic-route shell index.html
    match = _UUID_RE.match(path)
    if match:
        media_type = match.group(1)
        # Count UUIDs to decide season vs show shell
        uuid_count = len(
            re.findall(r"[0-9a-fA-F-]{8,}", path[len(f"/dashboard/{media_type}/") :])
        )
        if media_type == "shows" and uuid_count >= 2:
            shell = f"{FRONTEND_FILES_DIR}/dashboard/shows/_shell/_shell/index.html"
        else:
            shell = f"{FRONTEND_FILES_DIR}/dashboard/{media_type}/_shell/index.html"
        if Path(shell).is_file():  # noqa: ASYNC240 — cheap stat, intentional
            return FileResponse(shell)
    # Generic SPA fallback for anything that isn't an API route
    if not path.startswith("/api/"):
        fallback = f"{FRONTEND_FILES_DIR}/index.html"
        if Path(fallback).is_file():  # noqa: ASYNC240 — cheap stat, intentional
            return FileResponse(fallback)
    return Response(content="Not Found", status_code=404)


# Static frontend mounted at root LAST so explicit /api/* routes win route
# resolution. Without this ordering the mount would shadow API endpoints.
class CachedStaticFiles(StaticFiles):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        # Memoize which precompressed variants (.br / .gz) exist per asset so we
        # stat the filesystem at most once per path instead of on every request.
        # Frontend bundles are immutable for the process lifetime, so a stale
        # entry is not a concern.
        self._precompressed: dict[str, dict[str, Path]] = {}

    def _variants_for(self, path: str) -> dict[str, Path]:
        cached = self._precompressed.get(path)
        if cached is None:
            cached = {}
            br = Path(self.directory) / f"{path}.br"
            if br.is_file():
                cached["br"] = br
            gz = Path(self.directory) / f"{path}.gz"
            if gz.is_file():
                cached["gzip"] = gz
            self._precompressed[path] = cached
        return cached

    async def get_response(self, path: str, scope: Scope) -> Response:
        headers = Headers(scope=scope)
        if not path.endswith((".br", ".gz")):
            variants = self._variants_for(path)
            encoding = None
            compressed_path = None
            if "br" in headers.get("accept-encoding", "") and "br" in variants:
                encoding = "br"
                compressed_path = variants["br"]
            if (
                compressed_path is None
                and "gzip" in headers.get("accept-encoding", "")
                and "gzip" in variants
            ):
                encoding = "gzip"
                compressed_path = variants["gzip"]
            if compressed_path is not None and encoding is not None:
                media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                stat_result = await anyio.to_thread.run_sync(os.stat, compressed_path)
                resp = FileResponse(
                    compressed_path, media_type=media_type, stat_result=stat_result
                )
                resp.headers["Content-Encoding"] = encoding
                resp.headers["Vary"] = "Accept-Encoding"
                if path.startswith("_next/static/") or "/_next/static/" in path:
                    resp.headers["Cache-Control"] = (
                        "public, max-age=31536000, immutable"
                    )
                # Preserve StaticFiles' conditional-request behavior (304) that the
                # custom precompressed branch would otherwise bypass.
                if self.is_not_modified(resp.headers, headers):
                    return NotModifiedResponse(resp.headers)
                return resp
        resp = await super().get_response(path, scope)
        # Next.js hashed chunks under /_next/static/ are immutable forever.
        if path.startswith("_next/static/") or "/_next/static/" in path:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.endswith((".woff2", ".woff", ".ico", ".svg")):
            resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp


if not DISABLE_FRONTEND_MOUNT:
    app.mount(
        "/",
        CachedStaticFiles(
            directory=FRONTEND_FILES_DIR,
            html=True,
            follow_symlink=FRONTEND_FOLLOW_SYMLINKS,
        ),
        name="frontend",
    )
    log.debug(f"Mounted frontend at / from {FRONTEND_FILES_DIR}")
else:
    log.info("Frontend mounting disabled (DISABLE_FRONTEND_MOUNT is set)")


register_exception_handlers(app)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=5049,
        log_config=LOGGING_CONFIG,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )

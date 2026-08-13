import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import event
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from miramedia.database.config import DbConfig
from miramedia.exceptions import MiraMediaError

if TYPE_CHECKING:
    from miramedia.requests.repository import RequestRepository
    from miramedia.requests.service import RequestService

log = logging.getLogger(__name__)

Base = declarative_base()

engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None
# Dedicated background engine + sessionmaker. Scheduler tasks (auto-download,
# metadata refresh, scans) use this so they can't drain the request-facing
# pool while the user is interacting with the UI.
background_engine: AsyncEngine | None = None
SessionLocalBackground: async_sessionmaker[AsyncSession] | None = None
# Tiny NullPool engine reserved for the healthcheck so a saturated request /
# background pool never starves the liveness probe (which would flap the
# container under load). Opens a fresh connection per ping and discards it.
healthcheck_engine: AsyncEngine | None = None


def build_db_url(
    user: str,
    password: str,
    host: str,
    port: int | str,
    dbname: str,
    *,
    driver: str = "asyncpg",
) -> URL:
    """Build a SQLAlchemy URL with the requested driver.

    Defaults to ``asyncpg`` (the app + healthcheck engines). Pass
    ``driver="psycopg"`` for sync consumers — alembic env, taskiq broker —
    so all three subsystems agree on credential URL-encoding instead of
    each module reinventing it (which previously broke on ``@`` / ``%`` in
    passwords).
    """
    if driver == "psycopg":
        drivername = "postgresql+psycopg"
    elif driver == "psycopg_plain":
        # taskiq passes a raw ``postgresql://...`` DSN to libpq, not a
        # SQLAlchemy URL — accept the variant explicitly.
        drivername = "postgresql"
    else:
        drivername = "postgresql+asyncpg"
    return URL.create(
        drivername,
        user,
        password,
        host,
        int(port),
        dbname,
    )


def render_db_url(
    user: str,
    password: str,
    host: str,
    port: int | str,
    dbname: str,
    *,
    driver: str = "asyncpg",
) -> str:
    """String form of :func:`build_db_url` with credentials safely escaped."""
    return build_db_url(
        user, password, host, port, dbname, driver=driver
    ).render_as_string(hide_password=False)


def init_engine(
    db_config: DbConfig | None = None,
    url: str | URL | None = None,
) -> AsyncEngine:
    """Initialise the async engine + async sessionmaker. Idempotent.

    Also wires a separate ``background_engine`` + ``SessionLocalBackground``
    so taskiq tasks have their own connection pool. Without this, a busy
    scheduler can pin every connection in the request pool and make the UI
    feel hung while imports/metadata-refresh runs.

    A third ``healthcheck_engine`` uses ``NullPool`` so the liveness probe
    is decoupled from pool saturation in either of the other two pools.
    """
    global engine, SessionLocal, background_engine, SessionLocalBackground
    global healthcheck_engine
    if engine is not None:
        return engine

    if url is None:
        if db_config is None:
            url = os.getenv("DATABASE_URL")
            if not url:
                msg = "DB config or `DATABASE_URL` must be provided"
                raise RuntimeError(msg)
        else:
            url = build_db_url(
                db_config.user,
                db_config.password,
                db_config.host,
                db_config.port,
                db_config.dbname,
            )

    pool_size = int(os.getenv("MIRAMEDIA_DB_POOL_SIZE", "20"))
    max_overflow = int(os.getenv("MIRAMEDIA_DB_MAX_OVERFLOW", "20"))
    bg_pool_size = int(os.getenv("MIRAMEDIA_DB_BG_POOL_SIZE", "10"))
    bg_max_overflow = int(os.getenv("MIRAMEDIA_DB_BG_MAX_OVERFLOW", "10"))
    # ``pool_pre_ping=True`` issues a lightweight ``SELECT 1`` on checkout and
    # transparently recycles any connection that comes back dead. With asyncpg
    # the historical "different event loop" bug was fixed in SQLAlchemy 2.0;
    # the AsyncAdaptedQueuePool uses an asyncio-aware ping path. Combined with
    # ``pool_recycle`` (preempts long-idle conns) this gives defense in depth
    # against Postgres-side reaps (``idle_in_transaction_session_timeout``,
    # ``idle_session_timeout``) and network middlebox kills.
    # NOTE: asyncpg auto-prepares statements behind the scenes. That's safe
    # against a direct PG connection. If you ever front this with PgBouncer
    # in TRANSACTION pool mode, you MUST pass
    # ``connect_args={"statement_cache_size": 0}`` (and likely disable
    # ``pool_pre_ping`` in favour of client-side timeouts). Capture that
    # here so the change is one diff away, not a hunt through PR history.
    #
    # ``pool_recycle`` MUST stay below the server's
    # ``idle_in_transaction_session_timeout`` (docker-compose: 300s). At the
    # old 1800s the pool would hand back connections Postgres had already
    # reaped — pre-ping then races the reap and a held session can still die
    # mid-transaction. 240s recycles proactively with margin under the 300s
    # server cap. Tunable for deployments that change the server timeout.
    pool_recycle = int(os.getenv("MIRAMEDIA_DB_POOL_RECYCLE", "240"))
    engine = create_async_engine(
        url,
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=30,
        pool_recycle=pool_recycle,
        pool_pre_ping=True,
    )
    SessionLocal = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False, autocommit=False
    )
    background_engine = create_async_engine(
        url,
        echo=False,
        pool_size=bg_pool_size,
        max_overflow=bg_max_overflow,
        pool_timeout=60,
        pool_recycle=pool_recycle,
        pool_pre_ping=True,
    )
    SessionLocalBackground = async_sessionmaker(
        background_engine,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    # Healthcheck engine — NullPool so it never queues on a saturated
    # request/background pool. A flapping liveness probe would trigger
    # spurious container restarts exactly when the system is under load.
    from sqlalchemy.pool import NullPool

    healthcheck_engine = create_async_engine(
        url,
        echo=False,
        poolclass=NullPool,
    )
    _wire_pool_observability(engine, "request")
    _wire_pool_observability(background_engine, "background")
    log.debug(
        "Async SQLAlchemy engines initialised (request pool %d+%d, background pool %d+%d)",
        pool_size,
        max_overflow,
        bg_pool_size,
        bg_max_overflow,
    )
    return engine


_POOL_COUNTERS: dict[str, object] | None = None


def _get_pool_counters() -> dict[str, object] | None:
    """Return the singleton dict of Counter handles or ``None`` if
    ``prometheus_client`` isn't installed.

    Counters are created once per process. Uvicorn ``--reload`` re-imports
    this module on every code change; without the singleton-guard each
    reload would try to register the same metric name and raise
    ``ValueError: Duplicated timeseries in CollectorRegistry``.
    """
    global _POOL_COUNTERS
    if _POOL_COUNTERS is not None:
        return _POOL_COUNTERS
    try:
        from prometheus_client import REGISTRY, Counter
    except Exception:  # pragma: no cover
        return None

    def _counter(name: str, doc: str) -> object:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        return Counter(name, doc, ["pool"])

    _POOL_COUNTERS = {
        "checkout": _counter(
            "miramedia_db_pool_checkout_total",
            "Connections checked out from the pool",
        ),
        "checkin": _counter(
            "miramedia_db_pool_checkin_total",
            "Connections returned to the pool",
        ),
        "invalidate": _counter(
            "miramedia_db_pool_invalidate_total",
            "Connections invalidated (errored / reaped)",
        ),
        "connect": _counter(
            "miramedia_db_pool_connect_total",
            "Fresh DBAPI connects opened by the pool",
        ),
    }
    return _POOL_COUNTERS


def _wire_pool_observability(eng: AsyncEngine, label: str) -> None:
    """Attach SQLAlchemy pool events → Prometheus counters."""
    counters = _get_pool_counters()
    if counters is None:
        return

    sync_engine = eng.sync_engine

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(_dbapi: object, _record: object, _proxy: object) -> None:  # type: ignore[no-redef]
        counters["checkout"].labels(pool=label).inc()  # type: ignore[attr-defined]

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(_dbapi: object, _record: object) -> None:  # type: ignore[no-redef]
        counters["checkin"].labels(pool=label).inc()  # type: ignore[attr-defined]

    @event.listens_for(sync_engine, "invalidate")
    def _on_invalidate(_dbapi: object, _record: object, _exc: object) -> None:  # type: ignore[no-redef]
        counters["invalidate"].labels(pool=label).inc()  # type: ignore[attr-defined]

    @event.listens_for(sync_engine, "connect")
    def _on_connect(_dbapi: object, _record: object) -> None:  # type: ignore[no-redef]
        counters["connect"].labels(pool=label).inc()  # type: ignore[attr-defined]


_POOL_GAUGES: dict[str, object] | None = None


def _get_pool_gauges() -> dict[str, object] | None:
    """Singleton-guarded gauge registration — mirrors :func:`_get_pool_counters`
    so uvicorn ``--reload`` doesn't trip ``Duplicated timeseries``."""
    global _POOL_GAUGES
    if _POOL_GAUGES is not None:
        return _POOL_GAUGES
    try:
        from prometheus_client import REGISTRY, Gauge
    except Exception:  # pragma: no cover
        return None

    def _gauge(name: str, doc: str) -> object:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        return Gauge(name, doc, ["pool"])

    _POOL_GAUGES = {
        "size": _gauge("miramedia_db_pool_size", "Configured pool size"),
        "checked_out": _gauge(
            "miramedia_db_pool_checked_out", "Current checked-out connections"
        ),
        "overflow": _gauge(
            "miramedia_db_pool_overflow", "Current overflow connections"
        ),
    }
    return _POOL_GAUGES


def export_pool_gauges() -> None:
    """Refresh live pool gauges. Idempotent — safe per /metrics scrape."""
    gauges = _get_pool_gauges()
    if gauges is None:
        return
    for label, eng in (("request", engine), ("background", background_engine)):
        if eng is None:
            continue
        pool = eng.pool
        try:
            gauges["size"].labels(pool=label).set(pool.size())  # type: ignore[attr-defined]
            gauges["checked_out"].labels(pool=label).set(pool.checkedout())  # type: ignore[attr-defined]
            gauges["overflow"].labels(pool=label).set(pool.overflow())  # type: ignore[attr-defined]
        except Exception:  # noqa: S110 — best-effort gauge refresh, non-fatal  # pragma: no cover
            pass


def get_engine() -> AsyncEngine:
    if engine is None:
        msg = "Engine not initialized. Call init_engine(...) first."
        raise RuntimeError(msg)
    return engine


async def get_session() -> AsyncGenerator[AsyncSession]:
    if SessionLocal is None:
        msg = "Session factory not initialized. Call init_engine(...) first."
        raise RuntimeError(msg)
    async with SessionLocal() as db:
        try:
            yield db
            await db.commit()
        except (HTTPException, RequestValidationError, MiraMediaError):
            # Expected control-flow exceptions (404, 401, 403, 422 raised
            # from dependencies/routes, plus request-body validation failures
            # and the registered domain errors).
            # Roll back but don't log: a deleted show / stale link should
            # not flood logs with CRITICAL tracebacks.
            await db.rollback()
            raise
        except Exception:
            await db.rollback()
            log.exception("Unhandled exception during request DB session")
            raise


db_session: ContextVar[AsyncSession] = ContextVar("db_session")
DbSessionDependency = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Background service factories
# ---------------------------------------------------------------------------
# Production bug (2026-05-26): scheduler tasks declared services via
# ``TaskiqDepends(get_<svc>)``. The dep chain ultimately resolves the
# request-scoped ``get_session`` generator, which taskiq opens once at task
# start and tears down at task end — pinning a connection from the request
# pool in ``idle in transaction`` for the entire task duration.
#
# For tasks like ``scan_missing_subtitles_task`` that fan out to subliminal
# HTTP per-episode (multi-minute walls when providers are slow / timing out),
# this drains the pool until subsequent requests stall on ``pool_timeout``.
#
# Use these helpers from task bodies (or any long-running background coroutine
# that pauses on external I/O) to scope DB work tightly:
#
#     async def my_task() -> None:
#         async with bg_show_service() as show_service:
#             rows = await show_service.show_repository.get_shows()
#         # connection released here — slow loop holds nothing
#         for row in rows:
#             await slow_external_call(row)
#             async with bg_show_service() as show_service:
#                 await show_service.show_repository.stamp_metadata_check(row.id)
#                 await show_service.show_repository.db.commit()
#
# Each ``async with`` opens a short-lived ``SessionLocalBackground`` session,
# constructs the relevant service stack against it, and closes the session
# (returning the connection to the background pool) on exit.


def _require_background_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if SessionLocalBackground is None:
        msg = "Background session factory not initialized. Call init_engine(...) first."
        raise RuntimeError(msg)
    return SessionLocalBackground


async def release_session_before_external_io(db: AsyncSession) -> None:
    """Commit any pending TX + return connection to pool BEFORE slow external I/O.

    SQLAlchemy's ``AsyncSession`` checks out a connection lazily on the
    next statement, so it's safe to call this before a long external I/O
    ``await`` and continue using the session afterward. ``commit`` issues
    a ``COMMIT`` to close the implicit ``BEGIN`` that asyncpg opens on
    the first query; ``close`` then returns the connection to the pool.
    Per SQLAlchemy docs the session remains reusable — the next statement
    triggers a fresh checkout.

    Use this in any code path that holds a session and then ``await``s on
    something slow (subliminal HTTP, indexer fan-out + cloudflare bypass,
    libtorrent RPC, etc.). Without it the connection sits ``idle in
    transaction`` long enough for Postgres
    ``idle_in_transaction_session_timeout`` to reap the asyncpg socket,
    surfacing as ``InterfaceError: connection is closed`` on the next use
    and ``PendingRollbackError`` on the surrounding ``async with`` commit.

    Errors during commit/close are logged but swallowed: a failure here
    is strictly a "couldn't proactively release" — the slow I/O still
    needs to run, and any subsequent statement will surface a real error
    of its own.
    """
    try:
        await db.commit()
    except Exception:
        # Commit can fail when the connection was already reaped (e.g. a
        # slow filesystem scan held it ``idle in transaction`` past the
        # server cap before we got here). Do NOT return early: a dead
        # connection left attached to the session is reused verbatim by
        # the next statement — no fresh checkout, so ``pool_pre_ping``
        # never runs — and resurfaces as ``InterfaceError: connection is
        # closed``. Roll back to clear the failed-commit state and
        # invalidate the dead connection, then fall through to ``close``
        # so the next statement checks out a fresh, pre-pinged connection.
        log.exception("Failed to commit before external I/O")
        try:
            await db.rollback()
        except Exception:
            log.exception("Failed to roll back before external I/O")
    try:
        await db.close()
    except Exception:
        log.exception("Failed to release connection before external I/O")


async def release_sessions_before_external_io(
    *sessions: AsyncSession,
) -> None:
    """Release every distinct session before slow external I/O."""
    seen: set[int] = set()
    for db in sessions:
        token = id(db)
        if token in seen:
            continue
        seen.add(token)
        await release_session_before_external_io(db)


@asynccontextmanager
async def background_session() -> AsyncGenerator[AsyncSession]:
    """Yield a fresh ``SessionLocalBackground`` session.

    Auto-commits on clean exit, rolls back on exception. Use this directly
    when you only need raw repository access — for services with cross-cutting
    deps, use the ``bg_<svc>`` helpers below.
    """
    sessionmaker = _require_background_sessionmaker()
    async with sessionmaker() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@asynccontextmanager
async def bg_show_service() -> AsyncGenerator["ShowService"]:  # noqa: F821
    """Construct a ``ShowService`` backed by a short-lived background session."""
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        svc = ShowService(
            show_repository=ShowRepository(db),
            torrent_service=TorrentService(torrent_repository=TorrentRepository(db)),
            indexer_service=IndexerService(IndexerRepository(db)),
            notification_service=NotificationService(NotificationRepository(db)),
        )
        yield svc


@asynccontextmanager
async def bg_movie_service() -> AsyncGenerator["MovieService"]:  # noqa: F821
    """Construct a ``MovieService`` backed by a short-lived background session."""
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        svc = MovieService(
            movie_repository=MovieRepository(db),
            torrent_service=TorrentService(torrent_repository=TorrentRepository(db)),
            indexer_service=IndexerService(IndexerRepository(db)),
            notification_service=NotificationService(NotificationRepository(db)),
        )
        yield svc


@asynccontextmanager
async def bg_torrent_service() -> AsyncGenerator["TorrentService"]:  # noqa: F821
    """Construct a ``TorrentService`` backed by a short-lived background session."""
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        yield TorrentService(torrent_repository=TorrentRepository(db))


@asynccontextmanager
async def bg_request_service() -> AsyncGenerator[
    tuple["RequestService", "RequestRepository"]
]:
    """Construct a (RequestService, RequestRepository) pair backed by a
    short-lived background session.

    Returns a tuple because the fulfill_approved_requests_task uses both —
    the repository directly for Seerr reconcile, the service for the rest.
    """
    from miramedia.requests.backends.composite import CompositeRequestProvider
    from miramedia.requests.backends.native import NativeRequestProvider
    from miramedia.requests.dependencies import build_seerr_client
    from miramedia.requests.repository import RequestRepository
    from miramedia.requests.service import RequestService

    async with background_session() as db:
        repo = RequestRepository(db)
        native = NativeRequestProvider(repo)
        client = build_seerr_client()
        try:
            provider = CompositeRequestProvider(native, repo, client)
            yield RequestService(provider), repo
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    log.exception("Failed to close Seerr client in bg_request_service")


@asynccontextmanager
async def bg_subtitle_service() -> AsyncGenerator["SubtitleService"]:  # noqa: F821
    """Construct a ``SubtitleService`` backed by a short-lived background session.

    NOTE: the ``ShowService`` / ``MovieService`` it carries also share the same
    short-lived session. Callers must NOT hold the yielded service across slow
    external I/O — open a fresh ``bg_subtitle_service()`` for each unit of work.
    """
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.subtitles.repository import SubtitleRepository
    from miramedia.subtitles.service import SubtitleService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        notif = NotificationService(NotificationRepository(db))
        torrent = TorrentService(torrent_repository=TorrentRepository(db))
        indexer = IndexerService(IndexerRepository(db))
        show_svc = ShowService(
            show_repository=ShowRepository(db),
            torrent_service=torrent,
            indexer_service=indexer,
            notification_service=notif,
        )
        movie_svc = MovieService(
            movie_repository=MovieRepository(db),
            torrent_service=torrent,
            indexer_service=indexer,
            notification_service=notif,
        )
        yield SubtitleService(
            subtitle_repository=SubtitleRepository(db),
            show_service=show_svc,
            movie_service=movie_svc,
        )

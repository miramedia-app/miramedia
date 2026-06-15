from __future__ import annotations

import atexit
import contextlib
import logging
import os
import queue
import threading
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from miramedia.database.config import DbConfig

_sync_engine: Engine | None = None
_sync_session_maker: sessionmaker[Session] | None = None


def _get_sync_session_maker() -> sessionmaker[Session] | None:
    """Build a dedicated sync psycopg engine for the log handler.

    Decoupled from the app's async engine: the handler runs on its own
    background thread, so mixing it into the main asyncpg pool causes
    "Future attached to a different loop" errors. A small dedicated pool
    keeps logging fire-and-forget safe.
    """
    global _sync_engine, _sync_session_maker
    if _sync_session_maker is not None:
        return _sync_session_maker
    try:
        from miramedia.config import MiraMediaConfig
        from miramedia.database import build_db_url

        db_config: DbConfig = MiraMediaConfig().database
        # Sync engine — build_db_url defaults to asyncpg, which create_engine()
        # can't drive ("asyncio extension requires an async driver"). The handler
        # runs on its own thread with no event loop, so it needs the sync driver.
        url = build_db_url(
            user=db_config.user,
            password=db_config.password,
            host=db_config.host,
            port=db_config.port,
            dbname=db_config.dbname,
            driver="psycopg",
        )
        pool_size = int(os.getenv("MIRAMEDIA_LOG_POOL_SIZE", "5"))
        _sync_engine = create_engine(
            url, pool_size=pool_size, max_overflow=0, pool_recycle=1800, echo=False
        )
        _sync_session_maker = sessionmaker(
            _sync_engine, autocommit=False, autoflush=False
        )
    except Exception:
        return None
    else:
        return _sync_session_maker


class DatabaseLogHandler(logging.Handler):
    """Custom logging handler that batches log records into PostgreSQL.

    Uses a background thread + queue so emit() never blocks request threads.
    Writes via a dedicated sync psycopg engine — the app's async engine
    can't be safely used from a thread that isn't running an event loop.
    """

    # Drain ceiling per tick. Must exceed the steady-state miramedia.* record
    # rate or the queue grows unbounded under load (DEBUG/dev mode). 100/s was
    # the old ceiling and a dev-mode log storm outran it → queue + memory grew
    # without bound and never recovered.
    _DRAIN_PER_TICK = 1000

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        # Bounded so a log storm can't OOM the process. On overflow emit() drops
        # the record (logs are best-effort) instead of growing forever.
        maxsize = int(os.getenv("MIRAMEDIA_LOG_QUEUE_MAX", "20000"))
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        self._shutdown = threading.Event()
        self._suppress_until_ts: float = 0.0
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "id": uuid4(),
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC),
                "level": record.levelname,
                "module": record.name,
                "message": self.format(record),
                "correlation_id": getattr(record, "correlation_id", None),
            }
            try:
                self._queue.put_nowait(entry)
            except queue.Full:
                # Queue saturated — drop. Never block the calling (request)
                # thread, and never call handleError here: a storm would turn
                # into a stderr storm. The flush loop surfaces the drop count.
                self._dropped += 1
        except Exception:
            self.handleError(record)

    def _flush_loop(self) -> None:
        while not self._shutdown.is_set():
            self._shutdown.wait(timeout=1.0)
            self._flush()

    def _flush(self) -> None:
        batch: list[dict] = []
        while len(batch) < self._DRAIN_PER_TICK:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not batch:
            return

        if self._dropped:
            # Surface overflow once per flush instead of per dropped record.
            # Read-and-reset first so this warning's own record can't skew it.
            dropped, self._dropped = self._dropped, 0
            logging.getLogger(__name__).warning(
                "DatabaseLogHandler dropped %d log record(s): queue full", dropped
            )

        session_maker = _get_sync_session_maker()
        if session_maker is None:
            return
        # Best-effort DB write. Swallow any error (DB down, schema mismatch,
        # shutdown race) — logging must never raise into the flush thread, and
        # re-logging here would risk a feedback loop.
        with contextlib.suppress(Exception):
            from miramedia.logs.models import ActivityLog

            with session_maker() as db:
                db.bulk_insert_mappings(ActivityLog, batch)
                db.commit()

    def drain(self) -> None:
        """Discard all pending log entries in the queue."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def close(self) -> None:
        self._shutdown.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._flush()
        super().close()

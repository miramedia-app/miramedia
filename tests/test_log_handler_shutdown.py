"""Tests for DatabaseLogHandler shutdown drain behavior."""

from __future__ import annotations

import logging
import time
from typing import Self
from unittest.mock import patch

from miramedia.logs.handler import DatabaseLogHandler


class _RecordingSession:
    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = batches

    def bulk_insert_mappings(self, _model: object, batch: list[dict]) -> None:
        self._batches.append(batch)

    def commit(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class _RecordingSessionMaker:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def __call__(self) -> _RecordingSession:
        return _RecordingSession(self.batches)


def test_close_drains_queue_beyond_single_flush_tick() -> None:
    """Shutdown must flush more than _DRAIN_PER_TICK queued records."""
    maker = _RecordingSessionMaker()
    handler = DatabaseLogHandler(level=logging.INFO)
    total = 2500

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=maker):
        for i in range(total):
            record = logging.LogRecord(
                name="miramedia.test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="entry %s",
                args=(i,),
                exc_info=None,
            )
            handler.emit(record)

        handler.close()

    written = sum(len(batch) for batch in maker.batches)
    assert written == total
    assert len(maker.batches) > 1


def test_close_is_idempotent() -> None:
    """Second close() must not raise or re-flush an empty queue."""
    maker = _RecordingSessionMaker()
    handler = DatabaseLogHandler(level=logging.INFO)

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=maker):
        handler.emit(
            logging.LogRecord(
                name="miramedia.test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="once",
                args=(),
                exc_info=None,
            )
        )
        handler.close()
        handler.close()

    assert sum(len(batch) for batch in maker.batches) == 1


def test_close_returns_promptly_when_flush_makes_no_progress() -> None:
    """If _flush cannot drain the queue, close() must not spin until deadline."""
    handler = DatabaseLogHandler(level=logging.INFO)

    for i in range(10):
        handler.emit(
            logging.LogRecord(
                name="miramedia.test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="stuck %s",
                args=(i,),
                exc_info=None,
            )
        )

    with patch.object(handler, "_flush", return_value=None):
        start = time.monotonic()
        handler.close()
        elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert not handler._queue.empty()


def test_close_drains_queue_when_flush_progresses() -> None:
    """close() must empty the queue when _flush drains normally."""
    maker = _RecordingSessionMaker()
    handler = DatabaseLogHandler(level=logging.INFO)

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=maker):
        for i in range(5):
            handler.emit(
                logging.LogRecord(
                    name="miramedia.test",
                    level=logging.INFO,
                    pathname="test.py",
                    lineno=1,
                    msg="entry %s",
                    args=(i,),
                    exc_info=None,
                )
            )
        handler.close()

    assert handler._queue.empty()
    assert sum(len(batch) for batch in maker.batches) == 5


def _emit_records(handler: DatabaseLogHandler, count: int) -> None:
    for i in range(count):
        handler.emit(
            logging.LogRecord(
                name="miramedia.test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="entry %s",
                args=(i,),
                exc_info=None,
            )
        )


def test_flush_retains_queue_when_session_maker_unavailable() -> None:
    """Records must stay queued when the sync session maker is unavailable."""
    handler = DatabaseLogHandler(level=logging.INFO)
    _emit_records(handler, 3)

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=None):
        handler._flush()

    assert handler._queue.qsize() == 3


def test_flush_writes_retained_records_after_session_maker_recovers() -> None:
    """Queued records must flush once the session maker becomes available."""
    maker = _RecordingSessionMaker()
    handler = DatabaseLogHandler(level=logging.INFO)
    _emit_records(handler, 4)

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=None):
        handler._flush()
    assert handler._queue.qsize() == 4

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=maker):
        handler._flush()

    assert handler._queue.empty()
    assert sum(len(batch) for batch in maker.batches) == 4


def test_close_returns_promptly_when_session_maker_unavailable() -> None:
    """close() must not hang when the session maker never becomes available."""
    handler = DatabaseLogHandler(level=logging.INFO)
    _emit_records(handler, 10)

    with patch("miramedia.logs.handler._get_sync_session_maker", return_value=None):
        start = time.monotonic()
        handler.close()
        elapsed = time.monotonic() - start

    assert elapsed < 6.0
    assert handler._queue.qsize() == 10

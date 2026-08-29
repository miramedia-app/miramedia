"""In-process event bus for SSE fan-out.

One bus per process (single-instance app). Subscribers receive a fresh
``asyncio.Queue``; the bus drops messages on a slow consumer (bounded
queue) rather than blocking publishers.

Publish is sync + non-blocking — callers from sync code paths (e.g.
SQLAlchemy after_commit hooks, sync FastAPI handlers) can fire-and-forget
without awaiting. The bus prefers to drop a single event for a slow
client over stalling the whole app behind one stuck subscriber.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)


@dataclass
class Event:
    """A typed pub-sub message.

    ``type`` is the event name clients subscribe to (e.g. ``torrent.updated``).
    ``data`` stays small — just IDs; clients re-fetch the full payload via
    their existing REST endpoints, which keeps the SSE bytes-per-event
    bounded and avoids duplicating serialisation logic here.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """In-process fan-out hub. Not safe across processes — single-instance only."""

    # ``pg_notify`` rejects payloads larger than 8000 bytes. Leave headroom for
    # the JSON envelope (origin/type keys) and keep the cap comfortably below
    # the hard limit so a rare oversized ``data`` field never bursts NOTIFY.
    _NOTIFY_MAX_BYTES = 7000

    def __init__(self, queue_maxsize: int = 256) -> None:
        # ``dict`` preserves insertion order and our subscribe/unsubscribe
        # operations only touch the keys. We use a ``threading.Lock`` rather
        # than ``asyncio.Lock`` so ``publish()`` can take a consistent
        # snapshot when called from a worker thread (e.g. inside
        # ``anyio.to_thread.run_sync``) — an asyncio.Lock would either be
        # un-acquirable from a thread or only acquirable via a separate loop
        # call. Subscribe/unsubscribe run on the event loop, where a brief
        # uncontended threading.Lock acquire is essentially free.
        self._subscribers: dict[str, asyncio.Queue[Event]] = {}
        self._queue_maxsize = queue_maxsize
        self._lock = threading.Lock()
        self._origin_id = uuid4().hex
        # The loop the subscriber queues live on — captured on subscribe so an
        # off-loop publish() can hop onto it (see publish()).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbound: asyncio.Queue[Event] | None = None
        self._bridge_task: asyncio.Task | None = None

    async def subscribe(self) -> tuple[str, asyncio.Queue[Event]]:
        """Register a new subscriber.

        Returns the subscription id (for unsubscribe) and the queue the
        caller should ``await`` on. Each subscriber gets a fresh queue so
        slow consumers can't affect others.
        """
        sub_id = uuid4().hex
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_maxsize)
        with self._lock:
            self._subscribers[sub_id] = q
            self._loop = asyncio.get_running_loop()
        return sub_id, q

    async def unsubscribe(self, sub_id: str) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def publish(self, event: Event) -> None:
        """Non-blocking publish. Safe to call from any thread.

        ``asyncio.Queue`` is NOT thread-safe: ``put_nowait`` wakes waiting
        consumers via ``loop.call_soon`` (not the threadsafe variant), so a put
        from a worker thread would enqueue the event but never wake the SSE
        consumer. So when called off the consumers' loop thread (e.g. inside
        ``anyio.to_thread.run_sync`` or a sync after-commit hook) we hop onto
        that loop via ``call_soon_threadsafe``. On-loop callers deliver inline.
        Drops events for subscribers whose queue is full (better than stalling).
        """
        with self._lock:
            snapshot = list(self._subscribers.items())
            loop = self._loop
        if not snapshot:
            self._publish_cross_process(event)
            return
        try:
            on_loop = loop is not None and asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False  # no running loop → we're on a worker thread
        if on_loop or loop is None:
            self._deliver(snapshot, event)
        else:
            try:
                loop.call_soon_threadsafe(self._deliver, snapshot, event)
            except RuntimeError:
                # Loop already closed (shutdown) — nothing to deliver to.
                pass
        self._publish_cross_process(event)

    def _publish_cross_process(self, event: Event) -> None:
        q = self._outbound
        loop = self._loop
        if q is None or loop is None:
            return

        def _put() -> None:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.debug("Postgres event bridge queue full — dropping %s", event.type)

        try:
            if asyncio.get_running_loop() is loop:
                _put()
            else:
                loop.call_soon_threadsafe(_put)
        except RuntimeError:
            loop.call_soon_threadsafe(_put)

    def _deliver(
        self, snapshot: list[tuple[str, asyncio.Queue[Event]]], event: Event
    ) -> None:
        """Enqueue ``event`` to each subscriber. Runs on the loop thread."""
        dead: list[str] = []
        for sub_id, q in snapshot:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.debug(
                    "SSE subscriber %s queue full — dropping %s",
                    sub_id,
                    event.type,
                )
            except Exception:
                dead.append(sub_id)
        if dead:
            # Reap subscribers whose queue raised something unexpected.
            # Don't await unsubscribe here — publish() is sync.
            with self._lock:
                for sub_id in dead:
                    self._subscribers.pop(sub_id, None)

    async def stream(
        self, sub_id: str, q: asyncio.Queue[Event]
    ) -> AsyncIterator[Event]:
        """Async-iterate events for a subscriber. Cleans up on exit."""
        try:
            while True:
                yield await q.get()
        finally:
            await self.unsubscribe(sub_id)

    async def start_postgres_bridge(
        self, dsn: str, channel: str = "miramedia_events"
    ) -> None:
        """Bridge local events across workers via Postgres LISTEN/NOTIFY.

        This keeps the no-extra-container deployment model while making SSE
        invalidations safe if the app runs with multiple Uvicorn workers.
        """
        if self._bridge_task is not None and not self._bridge_task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._outbound = asyncio.Queue(maxsize=1024)
        self._bridge_task = asyncio.create_task(self._run_postgres_bridge(dsn, channel))

    async def stop_postgres_bridge(self) -> None:
        task = self._bridge_task
        self._bridge_task = None
        self._outbound = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _encode_notify_payload(self, event: Event) -> str:
        """Serialise ``event`` for NOTIFY, keeping it under the pg_notify cap.

        ``data`` is meant to carry only IDs (listeners re-fetch the detail via
        REST), but a stray large payload would blow past pg_notify's 8000-byte
        limit and make ``conn.execute`` raise — silently dropping the
        cross-worker invalidation. So if the full payload is too big we drop
        ``data`` and notify with the type alone; the listener still invalidates
        and re-fetches, just without the embedded hint.
        """
        payload = json.dumps(
            {
                "origin": self._origin_id,
                "type": event.type,
                "data": event.data,
            },
            default=str,
        )
        if len(payload.encode("utf-8")) <= self._NOTIFY_MAX_BYTES:
            return payload
        log.warning(
            "Postgres event payload for %s exceeds NOTIFY limit (%d bytes) — "
            "dropping data; listeners will re-fetch",
            event.type,
            len(payload.encode("utf-8")),
        )
        return json.dumps(
            {"origin": self._origin_id, "type": event.type, "data": {}},
            default=str,
        )

    async def _run_postgres_bridge(self, dsn: str, channel: str) -> None:
        import asyncpg

        # Retained across reconnects so a failed pg_notify can be retried.
        # At-least-once within this process only — listeners must tolerate
        # duplicate invalidation if we succeed at NOTIFY then crash before
        # clearing pending_event.
        pending_event: Event | None = None

        while True:
            conn = None
            try:
                conn = await asyncpg.connect(dsn)

                def _on_notify(
                    _conn: object, _pid: object, _channel: object, payload: str
                ) -> None:
                    try:
                        raw = json.loads(payload)
                        if raw.get("origin") == self._origin_id:
                            return
                        event = Event(type=raw["type"], data=raw.get("data") or {})
                    except Exception:
                        log.debug(
                            "Invalid Postgres event payload dropped", exc_info=True
                        )
                        return
                    with self._lock:
                        snapshot = list(self._subscribers.items())
                    if snapshot:
                        self._deliver(snapshot, event)

                await conn.add_listener(channel, _on_notify)
                log.info("Postgres event bridge listening on %s", channel)
                while True:
                    if pending_event is None:
                        pending_event = await self._outbound.get()  # type: ignore[union-attr]
                    payload = self._encode_notify_payload(pending_event)
                    await conn.execute("SELECT pg_notify($1, $2)", channel, payload)
                    pending_event = None
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Postgres event bridge failed; retrying")
                await asyncio.sleep(5)
            finally:
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:  # noqa: S110 — best-effort cleanup, non-fatal
                        pass


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide singleton accessor."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

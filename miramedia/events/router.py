"""SSE endpoint.

Clients connect once; the server pushes JSON-encoded events as they
happen. Replaces dashboard 2-5s polling loops.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from miramedia.auth.users import current_active_user
from miramedia.database import DbSessionDependency, release_session_before_external_io
from miramedia.events.bus import get_event_bus

log = logging.getLogger(__name__)
# Gate the stream: it fans out live bus events (torrent/import activity with
# media IDs) and holds a per-connection subscriber queue. Unauthenticated it
# leaked library activity and was an open resource vector. Browser EventSource
# sends the session cookie same-origin, so current_active_user resolves.
router = APIRouter(
    prefix="/events",
    tags=["events"],
    dependencies=[Depends(current_active_user)],
)


@router.get("/stream")
async def event_stream(db: DbSessionDependency) -> EventSourceResponse:
    """Long-lived SSE channel for dashboard state updates.

    Each client gets its own subscriber queue from the bus. We immediately
    emit a ``ready`` event so the browser knows the channel is live (and
    we can wire up react-query invalidations on connection rather than
    waiting for the first state change). ``ping`` keeps reverse proxies
    from closing the connection as idle.

    ``db`` is the request-scoped session shared (FastAPI dependency cache)
    with the ``current_active_user`` auth lookup. On an auth cache-miss that
    lookup leaves the connection ``idle in transaction``; held for the
    multi-minute SSE lifetime it gets reaped by Postgres
    ``idle_in_transaction_session_timeout`` and the ``get_session`` finalizer
    commit then dies on a closed connection. We don't touch the DB while
    streaming, so release the connection up front; the finalizer commit
    becomes a clean no-op.
    """
    bus = get_event_bus()
    sub_id, q = await bus.subscribe()
    await release_session_before_external_io(db)

    async def gen() -> AsyncGenerator[ServerSentEvent]:
        try:
            yield ServerSentEvent(event="ready", data="{}")
            async for ev in bus.stream(sub_id, q):
                yield ServerSentEvent(
                    event=ev.type,
                    data=json.dumps(ev.data, default=str),
                )
        except Exception:
            log.exception("SSE stream error")
        finally:
            await bus.unsubscribe(sub_id)

    # 15s heartbeat keeps proxies (nginx default idle 60s) from severing
    # connections during long quiet periods on the bus.
    return EventSourceResponse(gen(), ping=15000)

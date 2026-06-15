"""Lightweight pub-sub event bus + SSE router.

Used to push torrent / import state changes to connected clients so the
dashboard can drop the 2-5s polling loops.
"""

from miramedia.events.bus import Event, EventBus, get_event_bus
from miramedia.events.router import router

__all__ = ["Event", "EventBus", "get_event_bus", "router"]

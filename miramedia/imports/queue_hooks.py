"""Fire-and-forget import queue maintenance from sync code paths."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

log = logging.getLogger(__name__)

# Keep strong references to fire-and-forget tasks so they aren't GC'd mid-flight.
_background_tasks: set[asyncio.Task[None]] = set()

# Debounce handle for the coalesced full rebuild. Bursts of scan-cache / bulk
# import writes collapse into one rebuild whose DB session is opened only AFTER
# the debounce window elapses (see _debounced_rebuild).
_rebuild_debounce: asyncio.Task[None] | None = None
_REBUILD_DEBOUNCE_S = 2.0


def schedule_torrent_queue_sync(torrent_id: UUID) -> None:
    """Targeted queue row refresh for one torrent (import status change)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_torrent_queue_sync(torrent_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def schedule_import_queue_rebuild() -> None:
    """Debounced full rebuild after scan cache or bulk import changes."""
    global _rebuild_debounce
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # Coalesce a burst into one rebuild: a fresh call cancels the pending one
    # while it is still inside its debounce sleep (before any session is open).
    if _rebuild_debounce is not None and not _rebuild_debounce.done():
        _rebuild_debounce.cancel()
    task = loop.create_task(_debounced_rebuild())
    _rebuild_debounce = task
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _debounced_rebuild() -> None:
    # Sleep BEFORE opening the session so a later schedule can cancel us during
    # the window without ever holding a DB connection. The session is opened
    # once, at fire time, inside _rebuild_queue, so it stays alive for the whole
    # rebuild — unlike the old path that opened then closed it before the sleep.
    try:
        await asyncio.sleep(_REBUILD_DEBOUNCE_S)
    except asyncio.CancelledError:
        return
    await _rebuild_queue()


async def _torrent_queue_sync(torrent_id: UUID) -> None:
    try:
        from miramedia.database import (
            SessionLocalBackground,
            bg_movie_service,
            bg_show_service,
            bg_torrent_service,
        )
        from miramedia.imports.queue.refresh import sync_torrent_import_queue
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
                        await sync_torrent_import_queue(db, service, torrent_id)
    except Exception:
        log.exception("Torrent import queue sync failed for %s", torrent_id)


async def _rebuild_queue() -> None:
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
                        await rebuild_import_queue(db, service)
        # Scan/bulk import has no per-torrent SSE event of its own; push a
        # refresh so the imports dashboard refetches the rebuilt queue live
        # instead of waiting for a manual reload or the 5-min scheduler tick.
        from miramedia.events.bus import Event, get_event_bus

        get_event_bus().publish(Event(type="torrent.refresh"))
    except Exception:
        log.exception("Import queue rebuild failed")

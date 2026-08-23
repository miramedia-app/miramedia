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
_rebuild_waiting = False  # True while inside the debounce sleep (safe to cancel).
_rerun_requested = False  # Set when a schedule arrives during an active rebuild.
_REBUILD_DEBOUNCE_S = 2.0

# Coalesced per-reference incremental syncs (kind, ref_id).
_incremental_debounce: asyncio.Task[None] | None = None
_incremental_waiting = False
_incremental_flushing = False
_incremental_rerun = False
_pending_refs: dict[tuple[str, str], None] = {}
_pending_completions: dict[str, None] = {}
_INCREMENTAL_DEBOUNCE_S = 2.0


def schedule_torrent_queue_sync(torrent_id: UUID) -> None:
    """Targeted queue row refresh for one torrent (import status change)."""
    schedule_reference_queue_sync("torrent", str(torrent_id))


def schedule_scan_queue_sync(directory: str) -> None:
    """Targeted queue row refresh for one scan-cache directory."""
    schedule_reference_queue_sync("scan", directory)


def schedule_media_queue_sync(history_id: UUID) -> None:
    """Targeted queue row refresh for one torrent_history Done row."""
    schedule_reference_queue_sync("media", str(history_id))


def schedule_integrity_queue_sync(*, media_type: str, file_id: UUID) -> None:
    """Targeted queue row refresh for one integrity mismatch."""
    from miramedia.imports.queue.projector import integrity_ref_id

    schedule_reference_queue_sync("integrity", integrity_ref_id(media_type, file_id))


def schedule_import_completion_queue_sync(torrent_id: UUID) -> None:
    """Refresh torrent rows and any durable Done history row after import."""
    _pending_completions[str(torrent_id)] = None
    _schedule_incremental_flush()


def schedule_reference_queue_sync(kind: str, ref_id: str) -> None:
    """Coalesce bursts of per-reference queue maintenance."""
    _pending_refs[(kind, ref_id)] = None
    _schedule_incremental_flush()


def _schedule_incremental_flush() -> None:
    global _incremental_debounce, _incremental_rerun
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _incremental_debounce is not None and not _incremental_debounce.done():
        if _incremental_flushing:
            _incremental_rerun = True
            return
        _incremental_debounce.cancel()
    task = loop.create_task(_debounced_incremental_flush())
    _incremental_debounce = task
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def schedule_import_queue_rebuild() -> None:
    """Debounced full rebuild after scan cache or bulk import changes."""
    global _rebuild_debounce, _rerun_requested
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # Coalesce bursts during the debounce sleep by cancelling and restarting the
    # timer. Once a rebuild is running we never cancel it; record a rerun instead.
    if _rebuild_debounce is not None and not _rebuild_debounce.done():
        if _rebuild_waiting:
            _rebuild_debounce.cancel()
        else:
            _rerun_requested = True
            return
    task = loop.create_task(_debounced_rebuild())
    _rebuild_debounce = task
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _debounced_incremental_flush() -> None:
    global _incremental_waiting, _incremental_flushing, _incremental_rerun
    while True:
        _incremental_waiting = True
        try:
            await asyncio.sleep(_INCREMENTAL_DEBOUNCE_S)
        except asyncio.CancelledError:
            _incremental_waiting = False
            return
        _incremental_waiting = False
        _incremental_flushing = True
        try:
            await asyncio.shield(_flush_incremental_queue())
        finally:
            _incremental_flushing = False
        if not _incremental_rerun:
            break
        _incremental_rerun = False


async def _debounced_rebuild() -> None:
    global _rebuild_waiting, _rerun_requested
    while True:
        # Sleep BEFORE opening the session so a later schedule can cancel us
        # during the window without ever holding a DB connection.
        _rebuild_waiting = True
        try:
            await asyncio.sleep(_REBUILD_DEBOUNCE_S)
        except asyncio.CancelledError:
            _rebuild_waiting = False
            return
        _rebuild_waiting = False
        await asyncio.shield(_rebuild_queue())
        if not _rerun_requested:
            break
        _rerun_requested = False


async def _flush_incremental_queue() -> None:
    refs = list(_pending_refs.keys())
    completions = list(_pending_completions.keys())
    _pending_refs.clear()
    _pending_completions.clear()
    if not refs and not completions:
        return
    try:
        from miramedia.background_services import bg_imports_service
        from miramedia.imports.queue.refresh import (
            sync_import_completion_queue,
            sync_integrity_import_queue,
            sync_media_import_queue,
            sync_scan_import_queue,
            sync_torrent_import_queue,
        )

        async with bg_imports_service() as (db, service):
            for torrent_id in completions:
                await sync_import_completion_queue(db, service, UUID(torrent_id))
            for kind, ref_id in refs:
                if kind == "torrent":
                    await sync_torrent_import_queue(db, service, UUID(ref_id))
                elif kind == "scan":
                    await sync_scan_import_queue(db, service, ref_id)
                elif kind == "media":
                    await sync_media_import_queue(db, service, UUID(ref_id))
                elif kind == "integrity":
                    prefix = "integrity:"
                    body = ref_id.removeprefix(prefix)
                    media_type, _, file_id_str = body.partition(":")
                    await sync_integrity_import_queue(
                        db,
                        service,
                        media_type=media_type,
                        file_id=UUID(file_id_str),
                    )
                else:
                    log.warning(
                        "Unknown import queue reference %s/%s",
                        kind,
                        ref_id,
                    )
    except Exception:
        log.exception("Incremental import queue sync failed")


async def _rebuild_queue() -> None:
    try:
        from miramedia.background_services import bg_imports_service
        from miramedia.imports.queue.sync import rebuild_import_queue

        async with bg_imports_service() as (db, service):
            await rebuild_import_queue(db, service)
        # Scan/bulk import has no per-torrent SSE event of its own; push a
        # refresh so the imports dashboard refetches the rebuilt queue live
        # instead of waiting for a manual reload or the 5-min scheduler tick.
        from miramedia.events.bus import Event, get_event_bus

        get_event_bus().publish(Event(type="torrent.refresh"))
    except Exception:
        log.exception("Import queue rebuild failed")

"""Debounced + targeted import-queue index maintenance."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.database import release_session_before_external_io
from miramedia.imports.models import ImportQueueItem
from miramedia.imports.queue.projector import (
    ImportQueueItemLike,
    integrity_ref_id,
    project_queue_rows,
)
from miramedia.imports.queue.sync import acquire_import_queue_advisory_lock
from miramedia.imports.schemas import TorrentImportItem
from miramedia.imports.service import ImportsService

log = logging.getLogger(__name__)


def _rows_for_item(service: ImportsService, item: ImportQueueItemLike) -> list[dict]:
    return list(project_queue_rows(service, item).values())


async def remove_import_queue_reference(
    db: AsyncSession,
    *,
    kind: str,
    ref_id: str,
) -> None:
    """Drop every tab row for one queue reference."""
    await acquire_import_queue_advisory_lock(db)
    try:
        await db.execute(
            delete(ImportQueueItem).where(
                ImportQueueItem.kind == kind,
                ImportQueueItem.ref_id == ref_id,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def sync_import_queue_item(
    db: AsyncSession,
    service: ImportsService,
    item: ImportQueueItemLike,
) -> int:
    """Replace queue rows for one torrent, scan, media, or integrity reference."""
    await acquire_import_queue_advisory_lock(db)
    try:
        await db.execute(
            delete(ImportQueueItem).where(
                ImportQueueItem.kind == item.kind,
                ImportQueueItem.ref_id == item.id,
            )
        )
        rows = _rows_for_item(service, item)
        if rows:
            stmt = insert(ImportQueueItem.__table__)
            stmt = stmt.on_conflict_do_update(
                index_elements=["kind", "ref_id", "tab"],
                set_={
                    "bucket_rank": stmt.excluded.bucket_rank,
                    "sort_at": stmt.excluded.sort_at,
                    "payload": stmt.excluded.payload,
                },
            )
            await db.execute(stmt, rows)
        await db.commit()
        return len(rows)
    except Exception:
        await db.rollback()
        raise


async def sync_torrent_import_queue(
    db: AsyncSession,
    service: ImportsService,
    torrent_id: UUID,
) -> None:
    from miramedia.exceptions import NotFoundError
    from miramedia.torrents.schemas import TorrentId

    try:
        torrent = await service.torrent_service.torrent_repository.get_torrent_by_id(
            TorrentId(torrent_id)
        )
    except NotFoundError:
        await remove_import_queue_reference(db, kind="torrent", ref_id=str(torrent_id))
        return
    # Refresh live download status (persist=False — no event, no loop): a
    # torrent only belongs in the queue once its download has finished.
    await release_session_before_external_io(db)
    torrent = await service.torrent_service.get_torrent_status(torrent, persist=False)
    entry = await service.torrent_service._build_import_status_entry(torrent)
    if entry.progress.total == 0 or not service.torrent_service.is_import_ready(
        torrent
    ):
        await remove_import_queue_reference(db, kind="torrent", ref_id=str(torrent_id))
        return
    item = TorrentImportItem(
        id=str(torrent_id),
        entry=entry,
        backoff_seconds=service._backoff_seconds(entry),
    )
    await sync_import_queue_item(db, service, item)


async def sync_scan_import_queue(
    db: AsyncSession,
    service: ImportsService,
    directory: str,
) -> None:
    item = await service.build_scan_import_item(directory)
    if item is None:
        await remove_import_queue_reference(db, kind="scan", ref_id=directory)
        return
    await sync_import_queue_item(db, service, item)


async def sync_media_import_queue(
    db: AsyncSession,
    service: ImportsService,
    history_id: UUID,
) -> None:
    item = await service.build_media_import_item(history_id)
    if item is None:
        await remove_import_queue_reference(db, kind="media", ref_id=str(history_id))
        return
    await sync_import_queue_item(db, service, item)


async def sync_integrity_import_queue(
    db: AsyncSession,
    service: ImportsService,
    *,
    media_type: str,
    file_id: UUID,
) -> None:
    ref_id = integrity_ref_id(media_type, file_id)
    item = await service.build_integrity_import_item(
        media_type=media_type, file_id=file_id
    )
    if item is None:
        await remove_import_queue_reference(db, kind="integrity", ref_id=ref_id)
        return
    await sync_import_queue_item(db, service, item)


async def sync_import_completion_queue(
    db: AsyncSession,
    service: ImportsService,
    torrent_id: UUID,
) -> None:
    """Refresh torrent rows and any durable Done history row after import."""
    await sync_torrent_import_queue(db, service, torrent_id)
    history = await service.torrent_service.torrent_repository.get_torrent_history_by_torrent_id(
        torrent_id
    )
    if history is not None:
        from miramedia.torrents.schemas import TorrentHistoryOutcome

        if history.outcome == TorrentHistoryOutcome.imported.value:
            await sync_media_import_queue(db, service, history.id)

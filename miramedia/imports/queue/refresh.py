"""Debounced + targeted import-queue index maintenance."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.database import release_session_before_external_io
from miramedia.imports.models import ImportQueueItem
from miramedia.imports.schemas import ScanImportItem, TorrentImportItem
from miramedia.imports.service import ImportsService

log = logging.getLogger(__name__)


def _rows_for_item(
    service: ImportsService, item: TorrentImportItem | ScanImportItem
) -> list[dict]:
    from miramedia.imports.queue.sync import _queue_rows_for_item

    return list(_queue_rows_for_item(service, item).values())


async def sync_import_queue_item(
    db: AsyncSession,
    service: ImportsService,
    item: TorrentImportItem | ScanImportItem,
) -> None:
    """Replace queue rows for one torrent or scan directory."""
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
                "sort_at": stmt.excluded.sort_at,
                "payload": stmt.excluded.payload,
            },
        )
        await db.execute(stmt, rows)
    await db.commit()


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
        # Torrent row is gone (deleted mid-flight) — drop its stale queue rows
        # instead of erroring out and leaving them behind.
        await db.execute(
            delete(ImportQueueItem).where(
                ImportQueueItem.kind == "torrent",
                ImportQueueItem.ref_id == str(torrent_id),
            )
        )
        await db.commit()
        return
    # Refresh live download status (persist=False — no event, no loop): a
    # torrent only belongs in the queue once its download has finished.
    await release_session_before_external_io(db)
    torrent = await service.torrent_service.get_torrent_status(torrent, persist=False)
    entry = await service.torrent_service._build_import_status_entry(torrent)
    if entry.progress.total == 0 or not service.torrent_service.is_import_ready(
        torrent
    ):
        await db.execute(
            delete(ImportQueueItem).where(
                ImportQueueItem.kind == "torrent",
                ImportQueueItem.ref_id == str(torrent_id),
            )
        )
        await db.commit()
        return
    item = TorrentImportItem(
        id=str(torrent_id),
        entry=entry,
        backoff_seconds=service._backoff_seconds(entry),
    )
    await sync_import_queue_item(db, service, item)

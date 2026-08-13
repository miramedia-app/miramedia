"""Maintain the SQL-backed import queue index used by the imports UI."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.imports.models import ImportQueueItem
from miramedia.imports.queue.projector import project_queue_rows
from miramedia.imports.schemas import (
    ImportTab,
    IntegrityImportItem,
    ScanImportItem,
    TorrentImportItem,
)
from miramedia.imports.service import ImportsService

log = logging.getLogger(__name__)

# Transaction-scoped advisory lock for import-queue rebuilds and incremental
# reference syncs. Distinct from the scheduler singleton lock (4871260042).
IMPORT_QUEUE_REBUILD_ADVISORY_LOCK_KEY = 4871260043

_rebuild_lock = asyncio.Lock()


async def acquire_import_queue_advisory_lock(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": IMPORT_QUEUE_REBUILD_ADVISORY_LOCK_KEY},
    )


async def _acquire_import_queue_rebuild_advisory_lock(db: AsyncSession) -> None:
    await acquire_import_queue_advisory_lock(db)


async def import_queue_is_empty(db: AsyncSession) -> bool:
    total = int(
        (await db.scalar(select(func.count()).select_from(ImportQueueItem))) or 0
    )
    return total == 0


def _dedupe_import_items(
    items: list,
) -> list:
    """``_collect_items`` can surface the same ref twice; one INSERT batch cannot."""
    seen: set[tuple[str, str]] = set()
    out: list = []
    for item in items:
        key = (item.kind, item.id)
        if key in seen:
            log.warning("Skipping duplicate import queue source %s/%s", *key)
            continue
        seen.add(key)
        out.append(item)
    return out


def _queue_rows_for_item(
    service: ImportsService,
    item: TorrentImportItem | ScanImportItem | IntegrityImportItem,
) -> dict[tuple[str, str, str], dict]:
    return project_queue_rows(service, item)


async def rebuild_import_queue(
    db: AsyncSession,
    service: ImportsService,
    *,
    only_if_empty: bool = False,
) -> int:
    """Rebuild the entire queue from current torrent + scan state.

    When ``only_if_empty`` is True (cold-start populate path), a post-lock
    recheck skips redundant work if another worker already rebuilt while this
    one waited. Full rebuilds always repopulate after acquiring the lock.
    """
    async with _rebuild_lock:
        if only_if_empty:
            await _acquire_import_queue_rebuild_advisory_lock(db)
            if not await import_queue_is_empty(db):
                await db.rollback()
                return 0
            await db.rollback()

        items = _dedupe_import_items(await service._collect_items())

        await _acquire_import_queue_rebuild_advisory_lock(db)
        try:
            if only_if_empty and not await import_queue_is_empty(db):
                await db.rollback()
                return 0

            await db.execute(delete(ImportQueueItem))
            row_by_key: dict[tuple[str, str, str], dict] = {}
            for item in items:
                row_by_key.update(_queue_rows_for_item(service, item))
            rows = list(row_by_key.values())
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
            log.info("Rebuilt import queue with %d rows", len(rows))
            return len(rows)
        except Exception:
            await db.rollback()
            raise


async def list_queue_page(
    db: AsyncSession,
    *,
    tab: ImportTab,
    offset: int,
    limit: int,
    include_integrity: bool = False,
) -> tuple[list[dict], int]:
    # Integrity rows are superuser-only: filtered in SQL so pagination offsets
    # and totals stay consistent per role.
    tab_value = tab.value
    where = [ImportQueueItem.tab == tab_value]
    if not include_integrity:
        where.append(ImportQueueItem.kind != "integrity")
    count_stmt = select(func.count()).select_from(ImportQueueItem).where(*where)
    stmt = (
        select(ImportQueueItem.payload)
        .where(*where)
        .order_by(ImportQueueItem.bucket_rank.asc(), ImportQueueItem.sort_at.desc())
        .offset(offset)
        .limit(limit)
    )
    total = int((await db.scalar(count_stmt)) or 0)
    payloads = list((await db.execute(stmt)).scalars().all())
    return payloads, total


async def count_queue_by_tab(
    db: AsyncSession, *, include_integrity: bool = False
) -> dict[str, int]:
    stmt = select(ImportQueueItem.tab, func.count()).group_by(ImportQueueItem.tab)
    if not include_integrity:
        stmt = stmt.where(ImportQueueItem.kind != "integrity")
    rows = (await db.execute(stmt)).all()
    return {tab: int(count) for tab, count in rows}

"""Maintain the SQL-backed import queue index used by the imports UI."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.imports.models import ImportQueueItem
from miramedia.imports.schemas import (
    ImportTab,
    IntegrityImportItem,
    ScanImportItem,
    TorrentImportItem,
)
from miramedia.imports.service import ImportsService

log = logging.getLogger(__name__)

_rebuild_lock = asyncio.Lock()


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


def _bucket_rank(item: TorrentImportItem | ScanImportItem | IntegrityImportItem) -> int:
    """Status bucket order (Review=0, Retry=1, Done=2).

    Mirrors the frontend ``bucketOf`` grouping so the ``all`` tab lists every
    action-needed row before Done rows — reviewable scan + torrent items stay
    together on the first page instead of scattering behind chronologically
    newer imports.
    """
    if item.kind == "scan":
        return 2 if item.result.status == "imported" else 0
    if item.kind == "media":
        return 2
    if item.kind == "integrity":
        return 0
    p = item.entry.progress
    if p.failed > 0 or p.ambiguous > 0:
        return 0
    if p.all_imported:
        return 2
    if item.backoff_seconds is not None:
        return 1
    return 0


def _queue_rows_for_item(
    service: ImportsService,
    item: TorrentImportItem | ScanImportItem | IntegrityImportItem,
) -> dict[tuple[str, str, str], dict]:
    """Build at most one row per (kind, ref_id, tab)."""
    row_by_key: dict[tuple[str, str, str], dict] = {}
    tabs = (ImportTab.review, ImportTab.retry, ImportTab.done, ImportTab.all)
    rank = _bucket_rank(item)
    for tab in tabs:
        if not service._tab_matches(item, tab):
            continue
        sort_at = datetime.now(UTC)
        ref = item.id
        if item.kind == "torrent":
            ts = item.entry.progress.last_attempt_at
            if ts is not None:
                sort_at = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        elif item.kind == "media":
            ts = item.imported_at
            if ts is not None:
                sort_at = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        elif item.kind == "integrity":
            ts = item.mismatch.detected_at
            if ts is not None:
                sort_at = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        row_by_key[(item.kind, ref, tab.value)] = {
            "id": uuid.uuid4(),
            "kind": item.kind,
            "ref_id": ref,
            "tab": tab.value,
            "bucket_rank": rank,
            "sort_at": sort_at,
            "payload": item.model_dump(mode="json"),
        }
    return row_by_key


async def rebuild_import_queue(db: AsyncSession, service: ImportsService) -> int:
    """Rebuild the entire queue from current torrent + scan state."""
    async with _rebuild_lock:
        items = _dedupe_import_items(await service._collect_items())
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

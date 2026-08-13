"""Pure import-queue projection: one source item → tab rows.

Event → reference → tabs (incremental maintenance map)
-------------------------------------------------------
| Mutation / hook site                         | kind       | ref_id                         | tabs                    |
|----------------------------------------------|------------|--------------------------------|-------------------------|
| Torrent import-status change                 | torrent    | torrent UUID                   | review, retry, all      |
| Torrent import completes (history recorded)  | media      | torrent_history UUID           | done, all               |
| Scan cache row insert/update/delete          | scan       | scan directory path            | review or done, all     |
| Integrity mismatch appears                   | integrity  | integrity:{media}:{file_id}    | review, all             |
| Integrity dismiss / rebaseline               | integrity  | integrity:{media}:{file_id}    | (rows removed)          |
| replace_scan_cache, startup, periodic audit    | (full rebuild — not incremental)                               |
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from miramedia.imports.schemas import (
    ImportTab,
    IntegrityImportItem,
    MediaImportItem,
    ScanImportItem,
    TorrentImportItem,
)
from miramedia.imports.service import ImportsService

ImportQueueItemLike = (
    TorrentImportItem | ScanImportItem | MediaImportItem | IntegrityImportItem
)


def bucket_rank(item: ImportQueueItemLike) -> int:
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


def project_queue_rows(
    service: ImportsService,
    item: ImportQueueItemLike,
) -> dict[tuple[str, str, str], dict]:
    """Build at most one row per (kind, ref_id, tab)."""
    row_by_key: dict[tuple[str, str, str], dict] = {}
    tabs = (ImportTab.review, ImportTab.retry, ImportTab.done, ImportTab.all)
    rank = bucket_rank(item)
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


def integrity_ref_id(media_type: str, file_id: uuid.UUID) -> str:
    return f"integrity:{media_type}:{file_id}"

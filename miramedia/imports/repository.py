"""DB access for scan result cache + scan run singleton + ignored paths."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.imports.models import (
    IgnoredImportPath,
    ImportBatch,
    ScanResultCache,
    ScanRun,
)
from miramedia.imports.schemas import ScanRunState, ScanRunStatus

_SINGLETON_ID = "current"


def _queued_before(queued_at: str | None, cutoff: datetime) -> bool:
    """True if a row's ``queued_at`` is older than ``cutoff`` (so it's stale).

    A missing or unparseable timestamp is treated as stale — a queued row with
    no usable dispatch time has nothing keeping it alive, so reclaim it."""
    if not queued_at:
        return True
    try:
        ts = datetime.fromisoformat(queued_at)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts < cutoff


class ImportsRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---- scan result cache ------------------------------------------------

    async def replace_scan_cache(self, items: list[tuple[str, dict]]) -> None:
        """Wipe and re-populate the scan cache atomically.

        Single-statement bulk insert. Previously this loop-added one row
        per item which round-trips per row on flush.
        """
        await self.db.execute(delete(ScanResultCache))
        if items:
            now = datetime.now(UTC)
            rows = [
                {
                    "id": uuid.uuid4(),
                    "directory": directory,
                    "payload": payload,
                    "scanned_at": now,
                }
                for directory, payload in items
            ]
            await self.db.execute(insert(ScanResultCache), rows)
        await self.db.commit()
        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()

    async def list_scan_cache(self) -> list[dict]:
        stmt = select(ScanResultCache.payload, ScanResultCache.directory)
        result = await self.db.execute(stmt)
        return [{"directory": row.directory, **row.payload} for row in result.all()]

    async def delete_scan_cache_entry(self, directory: str) -> bool:
        stmt = delete(ScanResultCache).where(ScanResultCache.directory == directory)
        result = await self.db.execute(stmt)
        await self.db.commit()
        return (result.rowcount or 0) > 0

    async def count_queued_scans(self) -> int:
        """Scan rows handed to a background import worker (status "queued").

        Cheap COUNT backed by ``ix_scan_result_cache_payload_status``."""
        stmt = (
            select(func.count())
            .select_from(ScanResultCache)
            .where(ScanResultCache.payload["status"].astext == "queued")
        )
        return int((await self.db.scalar(stmt)) or 0)

    # ---- import batch progress counter ------------------------------------

    async def bump_import_batch_total(self, n: int = 1) -> None:
        """Atomically add ``n`` to the live batch total (the M in "N/M").

        Upsert so a fresh DB without the seeded singleton row still works."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(ImportBatch.__table__).values(id=_SINGLETON_ID, total=n)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={"total": ImportBatch.total + n},
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def reset_import_batch_if_idle(self) -> None:
        """Zero the batch total once no scan row is still queued — the batch is
        over. Single atomic statement so concurrent worker completions race
        safely (idempotent) and a mid-flight dispatch keeps a queued row alive,
        preventing a premature reset."""
        from sqlalchemy import text

        await self.db.execute(
            text(
                "UPDATE import_batch SET total = 0 "
                "WHERE id = :id AND total <> 0 AND NOT EXISTS ("
                "SELECT 1 FROM scan_result_cache "
                "WHERE payload->>'status' = 'queued')"
            ),
            {"id": _SINGLETON_ID},
        )
        await self.db.commit()

    async def get_import_batch_total(self) -> int:
        return int((await self.db.scalar(select(ImportBatch.total))) or 0)

    async def reclaim_stale_queued_imports(
        self, *, older_than: timedelta | None = None
    ) -> int:
        """Recover scan rows stuck in "queued" because their import worker died
        before reaching a terminal state (process restart / OOM kill — the
        per-task ``except`` that would mark them failed never runs).

        Flips them to "failed" with an explanatory error so they re-surface in
        Review and stay retryable, then resets the batch counter so the live
        "Importing N/M" toast can drain.

        ``older_than=None`` reclaims every queued row — used at startup, where no
        in-process worker can have survived the restart. A ``timedelta`` reclaims
        only rows queued before the cutoff (periodic sweep), leaving genuinely
        in-flight imports alone."""
        rows = (
            (
                await self.db.execute(
                    select(ScanResultCache).where(
                        ScanResultCache.payload["status"].astext == "queued"
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        cutoff = datetime.now(UTC) - older_than if older_than is not None else None
        reclaimed = 0
        for row in rows:
            payload = dict(row.payload)
            if cutoff is not None and not _queued_before(
                payload.get("queued_at"), cutoff
            ):
                continue
            payload["status"] = "failed"
            payload["import_error"] = (
                "Import was interrupted before completing (worker restarted). "
                "Press Import to retry."
            )
            payload.pop("queued_at", None)
            row.payload = payload
            reclaimed += 1
        if reclaimed == 0:
            return 0
        await self.db.commit()
        await self.reset_import_batch_if_idle()
        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return reclaimed

    async def mark_scan_cache_queued(self, directory: str) -> bool:
        """Mark a cached scan row as queued for import. The imports page hides
        the Import button while a row is in this state to prevent double-clicks
        and double-dispatches; the worker flips it to imported/failed when the
        background task completes."""
        result = await self.db.execute(
            select(ScanResultCache).where(ScanResultCache.directory == directory)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        payload = dict(row.payload)
        payload["status"] = "queued"
        payload.pop("import_error", None)
        # Stamp dispatch time so a periodic sweep can reclaim this row if its
        # worker dies mid-import and never reaches a terminal state.
        payload["queued_at"] = datetime.now(UTC).isoformat()
        row.payload = payload
        await self.db.commit()
        # New row entering the worker lane → grow the live batch total (M).
        await self.bump_import_batch_total(1)
        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return True

    async def mark_scan_cache_imported(
        self,
        directory: str,
        imported_name: str | None = None,
        imported_media_id: str | None = None,
        imported_media_type: str | None = None,
    ) -> bool:
        """Flip a cached scan row to status=imported (kept as a finished entry
        instead of being deleted). ``imported_media_id`` + ``imported_media_type``
        anchor the row to the library item so a later scan can tell whether the
        media still exists (and re-surface the dir if the user removed it)."""
        result = await self.db.execute(
            select(ScanResultCache).where(ScanResultCache.directory == directory)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        payload = dict(row.payload)
        payload["status"] = "imported"
        if imported_name:
            payload["imported_name"] = imported_name
        if imported_media_id:
            payload["imported_media_id"] = imported_media_id
        if imported_media_type:
            payload["imported_media_type"] = imported_media_type
        row.payload = payload  # reassign so SQLAlchemy flags the JSON dirty
        await self.db.commit()
        # Worker finished this row → reset the batch once nothing is queued.
        await self.reset_import_batch_if_idle()
        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return True

    async def mark_scan_cache_failed(
        self, directory: str, error: str | None = None
    ) -> bool:
        """Flip a cached scan row to status=failed (kept visible as a
        needs-attention entry, never counted as finished)."""
        result = await self.db.execute(
            select(ScanResultCache).where(ScanResultCache.directory == directory)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        payload = dict(row.payload)
        payload["status"] = "failed"
        payload["import_error"] = error
        row.payload = payload
        await self.db.commit()
        # Worker finished this row (failure is terminal) → maybe reset batch.
        await self.reset_import_batch_if_idle()
        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return True

    async def list_terminal_scan_cache(self) -> list[tuple[str, dict]]:
        """(directory, payload) pairs in a terminal state (imported or
        failed) — carried across a full rescan so finished / needs-attention
        rows don't vanish when the dir is no longer detected.

        Filter pushed into SQL via the functional ``payload->>'status'``
        index added in revision e2f3a4b5c6d7 — avoids a full table scan +
        Python-side filter on large libraries.
        """
        from sqlalchemy import text

        stmt = select(ScanResultCache.directory, ScanResultCache.payload).where(
            text("payload->>'status' IN ('imported','failed')")
        )
        result = await self.db.execute(stmt)
        return [(row.directory, row.payload) for row in result.all()]

    # ---- scan run singleton -----------------------------------------------

    async def get_scan_run(self) -> ScanRunStatus:
        row = await self.db.get(ScanRun, _SINGLETON_ID)
        if row is None:
            return ScanRunStatus()
        return ScanRunStatus(
            state=ScanRunState(row.state),
            started_at=row.started_at,
            finished_at=row.finished_at,
            items_found=row.items_found,
            last_error=row.last_error,
        )

    async def set_scan_run(
        self,
        *,
        state: ScanRunState,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        items_found: int | None = None,
        last_error: str | None = None,
    ) -> ScanRunStatus:
        row = await self.db.get(ScanRun, _SINGLETON_ID)
        if row is None:
            row = ScanRun(id=_SINGLETON_ID, state=state.value)
            self.db.add(row)
        row.state = state.value
        if started_at is not None:
            row.started_at = started_at
        if finished_at is not None:
            row.finished_at = finished_at
        if items_found is not None:
            row.items_found = items_found
        if last_error is not None or state != ScanRunState.error:
            row.last_error = last_error
        await self.db.commit()
        return await self.get_scan_run()

    # ---- ignored import paths ---------------------------------------------

    async def list_ignored_paths(self) -> list[str]:
        stmt = select(IgnoredImportPath.path)
        return list((await self.db.execute(stmt)).scalars().all())

    async def add_ignored_path(self, path: str) -> None:
        existing = (
            await self.db.execute(
                select(IgnoredImportPath).where(IgnoredImportPath.path == path)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self.db.add(
            IgnoredImportPath(
                id=uuid.uuid4(),
                path=path,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await self.db.commit()

    async def remove_ignored_path(self, path: str) -> bool:
        stmt = delete(IgnoredImportPath).where(IgnoredImportPath.path == path)
        result = await self.db.execute(stmt)
        await self.db.commit()
        return (result.rowcount or 0) > 0

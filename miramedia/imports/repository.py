"""DB access for scan result cache + scan run singleton + ignored paths."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.imports.models import (
    IgnoredImportPath,
    ImportBatch,
    ScanResultCache,
    ScanRun,
)
from miramedia.imports.schemas import ScanRunState, ScanRunStatus

_SINGLETON_ID = "current"

# Grace window before an unstarted queued row may be automatically reclaimed.
STALE_QUEUED_IMPORT_GRACE = timedelta(minutes=30)

# A worker that set worker_started_at but whose process died leaves the row
# permanently queued (no heartbeat exists to fence a live copy). Reclaim only
# after a grace long enough that no legitimate single-file NAS copy is still
# running. If a worker heartbeat is ever added (payload worker_heartbeat_at),
# shrink this grace and key on the heartbeat instead.
STALLED_WORKER_GRACE = timedelta(hours=6)

CLAIM_SCAN_CACHE_ROW_SQL = """
UPDATE scan_result_cache
SET payload = (payload - 'import_error' - 'claim_token' - 'worker_started_at')
    || jsonb_build_object(
        'status', 'queued',
        'queued_at', CAST(:queued_at AS text),
        'claim_token', CAST(:claim_token AS text)
    )
WHERE directory = :directory
  AND payload->>'status' IN ('pending', 'failed')
  AND payload->>'media_type_hint' = CAST(:media_type AS text)
RETURNING directory
"""

BEGIN_MANUAL_SCAN_WORKER_SQL = """
UPDATE scan_result_cache
SET payload = payload || jsonb_build_object(
    'worker_started_at', CAST(:worker_started_at AS text)
)
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'media_type_hint' = CAST(:media_type AS text)
  AND payload->>'claim_token' = CAST(:claim_token AS text)
  AND payload->>'worker_started_at' IS NULL
RETURNING directory
"""

SELECT_QUEUED_IMPORT_SNAPSHOT_SQL = """
SELECT directory,
       payload->>'claim_token' AS claim_token,
       payload->>'queued_at' AS queued_at
FROM scan_result_cache
WHERE payload->>'status' = 'queued'
  AND payload->>'worker_started_at' IS NULL
"""

STAMP_LEGACY_QUEUED_AT_SQL = """
UPDATE scan_result_cache
SET payload = payload || jsonb_build_object('queued_at', CAST(:queued_at AS text))
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'worker_started_at' IS NULL
  AND (
    (CAST(:expected_queued_at AS text) IS NULL AND payload->>'queued_at' IS NULL)
    OR payload->>'queued_at' = CAST(:expected_queued_at AS text)
  )
RETURNING directory
"""

RECLAIM_STALE_QUEUED_IMPORT_SQL = """
UPDATE scan_result_cache
SET payload = (payload - 'queued_at' - 'claim_token' - 'worker_started_at')
    || jsonb_build_object(
        'status', 'failed',
        'import_error', CAST(:error AS text)
    )
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'worker_started_at' IS NULL
  AND payload->>'queued_at' = CAST(:expected_queued_at AS text)
  AND (
    (CAST(:expected_claim_token AS text) IS NULL AND payload->>'claim_token' IS NULL)
    OR payload->>'claim_token' = CAST(:expected_claim_token AS text)
  )
RETURNING directory
"""

SELECT_STARTED_IMPORT_SNAPSHOT_SQL = """
SELECT directory,
       payload->>'claim_token' AS claim_token,
       payload->>'worker_started_at' AS worker_started_at
FROM scan_result_cache
WHERE payload->>'status' = 'queued'
  AND payload->>'worker_started_at' IS NOT NULL
"""

STAMP_LEGACY_WORKER_STARTED_AT_SQL = """
UPDATE scan_result_cache
SET payload = payload || jsonb_build_object(
    'worker_started_at', CAST(:worker_started_at AS text)
)
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'worker_started_at' IS NOT NULL
  AND (
    (CAST(:expected_worker_started_at AS text) IS NULL
     AND payload->>'worker_started_at' IS NULL)
    OR payload->>'worker_started_at' = CAST(:expected_worker_started_at AS text)
  )
RETURNING directory
"""

RECLAIM_STALLED_WORKER_IMPORT_SQL = """
UPDATE scan_result_cache
SET payload = (payload - 'queued_at' - 'claim_token' - 'worker_started_at')
    || jsonb_build_object(
        'status', 'failed',
        'import_error', CAST(:error AS text)
    )
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'worker_started_at' = CAST(:expected_worker_started_at AS text)
  AND (
    (CAST(:expected_claim_token AS text) IS NULL AND payload->>'claim_token' IS NULL)
    OR payload->>'claim_token' = CAST(:expected_claim_token AS text)
  )
RETURNING directory
"""

_STALE_RECLAIM_ERROR = (
    "Import was interrupted before completing (worker restarted). "
    "Press Import to retry."
)

_STALLED_WORKER_ERROR = (
    "Import worker did not finish (process died mid-import); retry the import"
)

COMPENSATE_SCAN_CACHE_CLAIM_SQL = """
UPDATE scan_result_cache
SET payload = (payload - 'queued_at' - 'claim_token' - 'worker_started_at')
    || jsonb_build_object(
        'status', 'failed',
        'import_error', CAST(:error AS text)
    )
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'claim_token' = CAST(:claim_token AS text)
  AND payload->>'worker_started_at' IS NULL
RETURNING directory
"""

COMPLETE_MANUAL_SCAN_IMPORT_SQL = """
UPDATE scan_result_cache
SET payload = (payload - 'queued_at' - 'claim_token' - 'worker_started_at')
    || jsonb_build_object(
        'status', 'imported',
        'imported_name', CAST(:imported_name AS text),
        'imported_media_id', CAST(:imported_media_id AS text),
        'imported_media_type', CAST(:imported_media_type AS text)
    )
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'claim_token' = CAST(:claim_token AS text)
  AND payload->>'worker_started_at' IS NOT NULL
RETURNING directory
"""

FAIL_MANUAL_SCAN_IMPORT_SQL = """
UPDATE scan_result_cache
SET payload = (payload - 'queued_at' - 'claim_token' - 'worker_started_at')
    || jsonb_build_object(
        'status', 'failed',
        'import_error', CAST(:error AS text)
    )
WHERE directory = :directory
  AND payload->>'status' = 'queued'
  AND payload->>'claim_token' = CAST(:claim_token AS text)
  AND payload->>'worker_started_at' IS NOT NULL
RETURNING directory
"""

RESET_IMPORT_BATCH_IF_IDLE_SQL = """
UPDATE import_batch SET total = 0
WHERE id = :id AND total <> 0 AND NOT EXISTS (
    SELECT 1 FROM scan_result_cache
    WHERE payload->>'status' = 'queued'
)
"""


class ScanClaimResult(enum.Enum):
    claimed = "claimed"
    not_found = "not_found"
    not_eligible = "not_eligible"


@dataclass(frozen=True)
class ScanClaimOutcome:
    result: ScanClaimResult
    claim_token: str | None = None


class ScanWorkerBeginResult(enum.Enum):
    started = "started"
    duplicate = "duplicate"
    stale = "stale"


@dataclass(frozen=True)
class ScanWorkerBeginOutcome:
    result: ScanWorkerBeginResult
    worker_started_at: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _queued_at_valid(queued_at: str | None) -> bool:
    if not queued_at:
        return False
    try:
        datetime.fromisoformat(queued_at)
    except ValueError:
        return False
    return True


def _queued_before(queued_at: str | None, cutoff: datetime) -> bool:
    """True if a parseable ``queued_at`` is older than ``cutoff``."""
    if not _queued_at_valid(queued_at):
        return False
    ts = datetime.fromisoformat(queued_at)  # type: ignore[arg-type]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts < cutoff


def _worker_started_at_valid(worker_started_at: str | None) -> bool:
    return _queued_at_valid(worker_started_at)


def _worker_started_before(worker_started_at: str | None, cutoff: datetime) -> bool:
    """True if a parseable ``worker_started_at`` is older than ``cutoff``."""
    return _queued_before(worker_started_at, cutoff)


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

    async def get_scan_cache_entry(self, directory: str) -> dict | None:
        """Exact-key lookup for one scan-cache row."""
        result = await self.db.execute(
            select(ScanResultCache.directory, ScanResultCache.payload).where(
                ScanResultCache.directory == directory
            )
        )
        row = result.first()
        if row is None:
            return None
        return {"directory": row.directory, **row.payload}

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

    async def _bump_import_batch_total_in_tx(self, n: int = 1) -> None:
        """Add ``n`` to the live batch total without committing."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(ImportBatch.__table__).values(id=_SINGLETON_ID, total=n)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={"total": ImportBatch.total + n},
        )
        await self.db.execute(stmt)

    async def bump_import_batch_total(self, n: int = 1) -> None:
        """Atomically add ``n`` to the live batch total (the M in "N/M").

        Upsert so a fresh DB without the seeded singleton row still works."""
        await self._bump_import_batch_total_in_tx(n)
        await self.db.commit()

    async def _decrement_import_batch_total_in_tx(self, n: int = 1) -> None:
        from sqlalchemy import text

        await self.db.execute(
            text(
                "UPDATE import_batch SET total = GREATEST(total - :n, 0) WHERE id = :id"
            ),
            {"id": _SINGLETON_ID, "n": n},
        )

    async def _reset_import_batch_if_idle_in_tx(self) -> None:
        await self.db.execute(
            text(RESET_IMPORT_BATCH_IF_IDLE_SQL),
            {"id": _SINGLETON_ID},
        )

    async def reset_import_batch_if_idle(self) -> None:
        """Zero the batch total once no scan row is still queued — the batch is
        over. Single atomic statement so concurrent worker completions race
        safely (idempotent) and a mid-flight dispatch keeps a queued row alive,
        preventing a premature reset."""
        await self._reset_import_batch_if_idle_in_tx()
        await self.db.commit()

    async def get_import_batch_total(self) -> int:
        return int((await self.db.scalar(select(ImportBatch.total))) or 0)

    async def reclaim_stale_queued_imports(self, *, older_than: timedelta) -> int:
        """Recover dispatched-but-not-started queued rows only.

        Two-tier automatic reclaim: unstarted rows use ``older_than`` (typically
        ``STALE_QUEUED_IMPORT_GRACE``); started rows are handled by
        :meth:`reclaim_stalled_worker_imports` with ``STALLED_WORKER_GRACE``.
        Legacy rows with missing/invalid ``queued_at`` are stamped once, then
        become eligible only after the normal grace interval.
        """
        cutoff = _utc_now() - older_than
        snapshot_result = await self.db.execute(text(SELECT_QUEUED_IMPORT_SNAPSHOT_SQL))

        stamped = 0
        reclaimed = 0
        try:
            for row in snapshot_result:
                queued_at = row.queued_at
                if not _queued_at_valid(queued_at):
                    result = await self.db.execute(
                        text(STAMP_LEGACY_QUEUED_AT_SQL),
                        {
                            "directory": row.directory,
                            "queued_at": _utc_now().isoformat(),
                            "expected_queued_at": queued_at,
                        },
                    )
                    if result.first() is not None:
                        stamped += 1
                    continue

                if not _queued_before(queued_at, cutoff):
                    continue

                result = await self.db.execute(
                    text(RECLAIM_STALE_QUEUED_IMPORT_SQL),
                    {
                        "directory": row.directory,
                        "expected_claim_token": row.claim_token,
                        "expected_queued_at": queued_at,
                        "error": _STALE_RECLAIM_ERROR,
                    },
                )
                if result.first() is not None:
                    reclaimed += 1

            if reclaimed == 0 and stamped == 0:
                return 0
            if reclaimed > 0:
                await self._reset_import_batch_if_idle_in_tx()
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return reclaimed

    async def reclaim_stalled_worker_imports(self, *, older_than: timedelta) -> int:
        """Recover queued rows whose worker died after ``begin_manual_scan_worker``.

        Rows with missing/invalid ``worker_started_at`` are stamped once, then
        become eligible only after the normal grace interval.
        """
        cutoff = _utc_now() - older_than
        snapshot_result = await self.db.execute(
            text(SELECT_STARTED_IMPORT_SNAPSHOT_SQL)
        )

        stamped = 0
        reclaimed = 0
        try:
            for row in snapshot_result:
                worker_started_at = row.worker_started_at
                if not _worker_started_at_valid(worker_started_at):
                    result = await self.db.execute(
                        text(STAMP_LEGACY_WORKER_STARTED_AT_SQL),
                        {
                            "directory": row.directory,
                            "worker_started_at": _utc_now().isoformat(),
                            "expected_worker_started_at": worker_started_at,
                        },
                    )
                    if result.first() is not None:
                        stamped += 1
                    continue

                if not _worker_started_before(worker_started_at, cutoff):
                    continue

                result = await self.db.execute(
                    text(RECLAIM_STALLED_WORKER_IMPORT_SQL),
                    {
                        "directory": row.directory,
                        "expected_claim_token": row.claim_token,
                        "expected_worker_started_at": worker_started_at,
                        "error": _STALLED_WORKER_ERROR,
                    },
                )
                if result.first() is not None:
                    reclaimed += 1

            if reclaimed == 0 and stamped == 0:
                return 0
            if reclaimed > 0:
                await self._reset_import_batch_if_idle_in_tx()
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return reclaimed

    async def claim_scan_cache_row(
        self, directory: str, *, media_type: str
    ) -> ScanClaimOutcome:
        """Atomically claim one cached scan row for background import.

        Eligible rows transition ``pending``/``failed`` -> ``queued`` in a
        single conditional UPDATE matching directory, retryable status, and
        ``media_type_hint``. A fresh ``claim_token`` is stored with the row.
        The batch counter grows in the same transaction.
        """
        queued_at = _utc_now().isoformat()
        claim_token = str(uuid.uuid4())
        try:
            result = await self.db.execute(
                text(CLAIM_SCAN_CACHE_ROW_SQL),
                {
                    "directory": directory,
                    "queued_at": queued_at,
                    "media_type": media_type,
                    "claim_token": claim_token,
                },
            )
            if result.first() is None:
                await self.db.rollback()
                exists = await self.db.scalar(
                    select(ScanResultCache.directory).where(
                        ScanResultCache.directory == directory
                    )
                )
                if exists is None:
                    return ScanClaimOutcome(ScanClaimResult.not_found)
                return ScanClaimOutcome(ScanClaimResult.not_eligible)

            await self._bump_import_batch_total_in_tx(1)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return ScanClaimOutcome(ScanClaimResult.claimed, claim_token=claim_token)

    async def compensate_scan_cache_claim(
        self, directory: str, *, claim_token: str, error: str
    ) -> bool:
        """Undo a queued claim when broker dispatch fails.

        Flips this exact row from ``queued`` to retryable ``failed`` when the
        claim token matches, decrements the batch total, and zeros the batch
        counter when no queued rows remain — all in one transaction.
        """
        try:
            result = await self.db.execute(
                text(COMPENSATE_SCAN_CACHE_CLAIM_SQL),
                {
                    "directory": directory,
                    "claim_token": claim_token,
                    "error": error,
                },
            )
            if result.first() is None:
                await self.db.rollback()
                return False
            await self._decrement_import_batch_total_in_tx(1)
            await self._reset_import_batch_if_idle_in_tx()
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return True

    async def begin_manual_scan_worker(
        self, directory: str, *, claim_token: str, media_type: str
    ) -> ScanWorkerBeginOutcome:
        """Atomically mark one queued manual resolve as worker-started.

        Only the first delivery with a matching claim token may proceed to
        filesystem mutation. Duplicate deliveries observe an existing
        ``worker_started_at`` and return without touching the row.
        """
        worker_started_at = _utc_now().isoformat()
        result = await self.db.execute(
            text(BEGIN_MANUAL_SCAN_WORKER_SQL),
            {
                "directory": directory,
                "claim_token": claim_token,
                "media_type": media_type,
                "worker_started_at": worker_started_at,
            },
        )
        if result.first() is not None:
            await self.db.commit()
            return ScanWorkerBeginOutcome(
                ScanWorkerBeginResult.started,
                worker_started_at=worker_started_at,
            )

        row = await self.get_scan_cache_entry(directory)
        if row is None:
            return ScanWorkerBeginOutcome(ScanWorkerBeginResult.stale)
        if (
            row.get("status") == "queued"
            and row.get("claim_token") == claim_token
            and row.get("media_type_hint") == media_type
            and row.get("worker_started_at") is not None
        ):
            return ScanWorkerBeginOutcome(ScanWorkerBeginResult.duplicate)
        return ScanWorkerBeginOutcome(ScanWorkerBeginResult.stale)

    async def complete_manual_scan_import(
        self,
        directory: str,
        *,
        claim_token: str,
        imported_name: str | None = None,
        imported_media_id: str | None = None,
        imported_media_type: str | None = None,
    ) -> bool:
        """Terminal imported write for a manual resolve, CAS on claim token."""
        try:
            result = await self.db.execute(
                text(COMPLETE_MANUAL_SCAN_IMPORT_SQL),
                {
                    "directory": directory,
                    "claim_token": claim_token,
                    "imported_name": imported_name,
                    "imported_media_id": imported_media_id,
                    "imported_media_type": imported_media_type,
                },
            )
            if result.first() is None:
                await self.db.rollback()
                return False
            await self._reset_import_batch_if_idle_in_tx()
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return True

    async def fail_manual_scan_import(
        self, directory: str, *, claim_token: str, error: str | None = None
    ) -> bool:
        """Terminal failed write for a manual resolve, CAS on claim token."""
        try:
            result = await self.db.execute(
                text(FAIL_MANUAL_SCAN_IMPORT_SQL),
                {
                    "directory": directory,
                    "claim_token": claim_token,
                    "error": error,
                },
            )
            if result.first() is None:
                await self.db.rollback()
                return False
            await self._reset_import_batch_if_idle_in_tx()
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

        schedule_import_queue_rebuild()
        return True

    async def mark_scan_cache_queued(self, directory: str) -> bool:
        """Deprecated alias for :meth:`claim_scan_cache_row`."""
        row = await self.get_scan_cache_entry(directory)
        media_type = (row or {}).get("media_type_hint")
        if not isinstance(media_type, str):
            return False
        outcome = await self.claim_scan_cache_row(directory, media_type=media_type)
        return outcome.result is ScanClaimResult.claimed

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

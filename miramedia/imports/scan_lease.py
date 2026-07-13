"""Worker lease timing and heartbeat for manual scan imports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Rows with no heartbeat refresh for this long may be reclaimed.
STALE_QUEUED_IMPORT_GRACE = timedelta(minutes=30)
# Refresh the lease well inside the grace window (6 beats per grace period).
SCAN_WORKER_HEARTBEAT_INTERVAL = timedelta(minutes=5)

SleepFn = Callable[[float], Awaitable[None]]
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class ScanWorkerLease:
    directory: str
    claim_token: str
    media_type: str
    worker_started_at: str


class ScanWorkerLeaseHeartbeat:
    """Refresh ``worker_started_at`` while a scan import mutates filesystem state.

    Uses independent short ``background_session`` scopes so heartbeats never
    hold the task worker's primary session across slow I/O.
    """

    def __init__(
        self,
        lease: ScanWorkerLease,
        *,
        interval: timedelta = SCAN_WORKER_HEARTBEAT_INTERVAL,
        sleep: SleepFn = _default_sleep,
        session_factory: SessionFactory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._lease = lease
        self._directory = lease.directory
        self._claim_token = lease.claim_token
        self._media_type = lease.media_type
        self._worker_started_at = lease.worker_started_at
        self._interval = interval
        self._sleep = sleep
        self._session_factory = session_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self._run(), name="scan-worker-heartbeat")
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def pulse(self) -> bool:
        """Send one heartbeat (for deterministic tests)."""
        return await self._heartbeat_once()

    async def _run(self) -> None:
        interval_s = self._interval.total_seconds()
        while not self._stop.is_set():
            await self._sleep(interval_s)
            if self._stop.is_set():
                break
            await self._heartbeat_once()

    async def _heartbeat_once(self) -> bool:
        from miramedia.database import background_session
        from miramedia.imports.repository import ImportsRepository

        session_factory = self._session_factory or background_session
        refreshed_at = self._now().isoformat()
        async with session_factory() as db:
            repo = ImportsRepository(db=db)
            ok = await repo.heartbeat_manual_scan_worker(
                self._directory,
                claim_token=self._claim_token,
                media_type=self._media_type,
                expected_worker_started_at=self._worker_started_at,
                worker_started_at=refreshed_at,
            )
        if ok:
            self._worker_started_at = refreshed_at
        else:
            log.info(
                "Scan worker heartbeat lost lease for %s",
                self._directory,
            )
        return ok

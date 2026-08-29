"""Per-loop asyncio locks shared by import and integrity sweeps."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

_IMPORT_SWEEP_LOCKS: dict[str, asyncio.Lock] = {}


def import_sweep_lock(key: str) -> asyncio.Lock:
    lock = _IMPORT_SWEEP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _IMPORT_SWEEP_LOCKS[key] = lock
    return lock


async def run_unless_locked(
    key: str,
    action: Callable[[], Awaitable[None]],
    *,
    skip_message: str,
) -> None:
    lock = import_sweep_lock(key)
    if lock.locked():
        log.debug(skip_message)
        return
    async with lock:
        await action()

"""Per-loop asyncio locks shared by import and integrity sweeps."""

from __future__ import annotations

import asyncio

_IMPORT_SWEEP_LOCKS: dict[str, asyncio.Lock] = {}


def import_sweep_lock(key: str) -> asyncio.Lock:
    lock = _IMPORT_SWEEP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _IMPORT_SWEEP_LOCKS[key] = lock
    return lock

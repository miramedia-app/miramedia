"""Shared disk-scan cache and list-view helpers for show/movie services."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path

from miramedia.disk_scan_cache import (
    _DISK_SCAN_CACHE_TTL,
    DISK_SCAN_CACHE_TTL,
    _scan_cache,
    _scan_cache_lock,
    invalidate_disk_scan_cache,
    scan_cache,
    scan_cache_lock,
)

__all__ = [
    "DISK_SCAN_CACHE_TTL",
    "DISK_SCAN_CONCURRENCY",
    "_DISK_SCAN_CACHE_TTL",
    "_DISK_SCAN_CONCURRENCY",
    "_scan_cache",
    "_scan_cache_lock",
    "invalidate_disk_scan_cache",
    "scan_cache",
    "scan_cache_lock",
    "scan_rows_for_files",
]


def _disk_scan_concurrency() -> int:
    try:
        return max(1, int(os.getenv("MIRAMEDIA_DISK_SCAN_CONCURRENCY", "8")))
    except (TypeError, ValueError):
        return 8


# Cap concurrent directory scans on the list endpoints. Unbounded, a large
# library fans out hundreds of stat-heavy threads at once, saturating the anyio
# threadpool (so every other to_thread queues) and the NAS disk — the "idle CPU
# but slow" symptom. Mirrors the torrents-list RPC semaphore.
DISK_SCAN_CONCURRENCY = _disk_scan_concurrency()
_DISK_SCAN_CONCURRENCY = DISK_SCAN_CONCURRENCY


def scan_rows_for_files[K, T](
    directory: Path,
    rows: Iterable[T],
    *,
    key: Callable[[T], K],
    stems: Callable[[T], list[str]],
    video_exts: frozenset[str],
) -> dict[K, str]:
    """Map each row to the first on-disk video filename matching its stem prefix."""
    out: dict[K, str] = {}
    if not directory.exists() or not directory.is_dir():
        return out
    try:
        entries = list(directory.iterdir())
    except OSError:
        return out
    for row in rows:
        row_key = key(row)
        for stem in stems(row):
            prefix = stem + "."
            for path in entries:
                if (
                    path.is_file()
                    and path.name.startswith(prefix)
                    and path.suffix.lower() in video_exts
                ):
                    out[row_key] = path.name
                    break
            if row_key in out:
                break
    return out

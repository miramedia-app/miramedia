"""Shared disk-scan cache and list-view helpers for show/movie services."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from cachetools import TTLCache


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


def _disk_scan_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("MIRAMEDIA_DISK_SCAN_CACHE_TTL", "30")))
    except (TypeError, ValueError):
        return 30.0


# Short-TTL cache for season/movie-root scans. The dashboard re-fetches the
# shows/movies list on navigation/poll; without this every load re-scans the
# whole library off the NAS disk. Disk contents change only on import/delete, so
# a few seconds of staleness on the download badge is fine. Keyed by dir path
# or (dir, stems) tuple; thread-safe (the scan runs in to_thread worker threads).
# TTL=0 disables. Bumped on import via invalidate_disk_scan_cache().
DISK_SCAN_CACHE_TTL = _disk_scan_cache_ttl()
_DISK_SCAN_CACHE_TTL = DISK_SCAN_CACHE_TTL
scan_cache: TTLCache = TTLCache(maxsize=8192, ttl=_DISK_SCAN_CACHE_TTL or 1)
_scan_cache = scan_cache
scan_cache_lock = threading.Lock()
_scan_cache_lock = scan_cache_lock


def invalidate_disk_scan_cache() -> None:
    """Drop all cached season/movie scans (call after an import/delete mutates disk)."""
    with scan_cache_lock:
        scan_cache.clear()


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

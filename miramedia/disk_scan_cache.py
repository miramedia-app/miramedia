"""Dependency-free disk-scan cache state shared by show/movie list endpoints."""

from __future__ import annotations

import os
import threading

from cachetools import TTLCache


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

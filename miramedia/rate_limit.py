"""In-process sliding-window rate limiting for auth and integration endpoints.

Buckets are per-process only: with multiple Uvicorn workers the effective
limit scales roughly with worker count unless ``effective_budget`` divides
the configured ceiling per process. Keys are pruned when their window
expires; when the key map exceeds ``_MAX_KEYS`` least-recently-used cold
buckets are evicted first to bound memory.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, status

log = logging.getLogger(__name__)

_MAX_KEYS = 10_000


def configured_workers() -> int:
    """Return the configured Uvicorn worker count (min 1, invalid env → 1)."""
    raw = os.getenv("MIRAMEDIA_WEB_WORKERS", "1") or "1"
    try:
        workers = int(raw)
    except ValueError:
        log.warning(
            "Invalid MIRAMEDIA_WEB_WORKERS=%r; treating as 1 worker for rate limits",
            raw,
        )
        workers = 1
    return max(1, workers)


def effective_budget(max_requests: int) -> int:
    """Per-process budget: configured budget divided by worker count (min 1)."""
    return max(1, max_requests // configured_workers())


class SlidingWindowLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        detail_template: str = "Too many requests. Try again in {retry_in}s.",
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._detail_template = detail_template
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Record an attempt for ``key``; raise HTTP 429 when over limit."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._evict_if_needed(now)
                bucket = deque()
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)

            cutoff = now - self._window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

            if len(bucket) >= self._max_requests:
                retry_in = int(self._window_seconds - (now - bucket[0]))
                retry_after = max(1, retry_in)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=self._detail_template.format(retry_in=retry_after),
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

    def _bucket_at_limit(self, bucket: deque[float], now: float) -> bool:
        cutoff = now - self._window_seconds
        count = sum(1 for timestamp in bucket if timestamp >= cutoff)
        return count >= self._max_requests

    def _evict_if_needed(self, now: float) -> None:
        while len(self._buckets) >= _MAX_KEYS:
            for key in self._buckets:
                bucket = self._buckets[key]
                if self._bucket_at_limit(bucket, now):
                    continue
                del self._buckets[key]
                break
            else:
                # Every bucket is at limit — evict LRU anyway to bound memory.
                for oldest_key in self._buckets:
                    del self._buckets[oldest_key]
                    break

"""Deadline-aware GET retries for native indexer HTTP clients.

Small, idempotent policy: at most one retry per origin/path by default, bounded
backoff, and a shared monotonic deadline across mirrors/paths/detail fetches.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, cast

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 502, 503, 504})

DEFAULT_MAX_RETRIES = 1
DEFAULT_BASE_DELAY = 0.5
DEFAULT_CAP_DELAY = 2.0

# Absolute ceiling on server-directed Retry-After waits. A hostile/broken host
# must not be able to pin a fan-out worker thread in time.sleep for hours.
MAX_RETRY_AFTER = 30.0

_RETRY_AFTER_SECONDS_RE = re.compile(r"^\d+$")


class IndexerDeadlineError(Exception):
    """Raised when the shared indexer fan-out deadline is exhausted."""


def indexer_fanout_deadline_seconds() -> float:
    return float(os.getenv("MIRAMEDIA_INDEXER_FANOUT_TIMEOUT", "300"))


def indexer_fanout_deadline(
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> float:
    return monotonic() + indexer_fanout_deadline_seconds()


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse ``Retry-After`` as delta-seconds or HTTP-date; return seconds or None."""
    if not value:
        return None
    trimmed = value.strip()
    if _RETRY_AFTER_SECONDS_RE.fullmatch(trimmed):
        return float(trimmed)
    try:
        retry_at = parsedate_to_datetime(trimmed)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return max(0.0, (retry_at - reference).total_seconds())


def backoff_delay(
    attempt: int,
    base_delay: float,
    cap_delay: float,
    *,
    retry_after: float | None = None,
    rand: Callable[[], float] = random.random,
) -> float:
    """Bounded full jitter; honor server ``Retry-After`` as a floor, capped at ``MAX_RETRY_AFTER``."""
    exp = min(cap_delay, base_delay * (2**attempt))
    jittered = rand() * exp
    if retry_after is not None:
        return min(max(jittered, retry_after), MAX_RETRY_AFTER)
    return jittered


def should_fast_fail_response(response: httpx.Response) -> bool:
    """Cloudflare/DDOS challenge pages must not be retried on the plain path."""
    from miramedia.cloudflare.bypass import is_cloudflare_challenge

    if is_cloudflare_challenge(cast(Any, response)):
        return True

    if response.status_code == 503:
        server = response.headers.get("server", "").lower()
        if "ddos-guard" in server or "cloudflare" in server:
            return True
    return False


def _remaining_budget(
    deadline: float | None,
    monotonic: Callable[[], float],
) -> float | None:
    if deadline is None:
        return None
    return deadline - monotonic()


def _clamp_timeout(
    timeout: float | httpx.Timeout | None,
    remaining: float | None,
) -> float | httpx.Timeout | None:
    if remaining is None:
        return timeout
    if remaining <= 0:
        return 0.0
    if timeout is None:
        return remaining
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return min(float(timeout), remaining)


def _wait(
    delay: float,
    *,
    deadline: float | None,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    remaining = _remaining_budget(deadline, monotonic)
    if remaining is not None:
        if remaining <= 0:
            return False
        delay = min(delay, remaining)
    if delay > 0:
        sleep(delay)
    if deadline is not None and monotonic() >= deadline:
        return False
    return True


def indexer_get(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, object] | None = None,
    timeout: float | httpx.Timeout | None = None,
    deadline: float | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    cap_delay: float = DEFAULT_CAP_DELAY,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    now: datetime | None = None,
) -> httpx.Response:
    """GET with bounded transient retries, respecting a shared monotonic deadline."""
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        remaining = _remaining_budget(deadline, monotonic)
        if remaining is not None and remaining <= 0:
            msg = "shared indexer deadline exhausted before request"
            raise IndexerDeadlineError(msg)

        req_timeout = _clamp_timeout(timeout, remaining)
        try:
            response = client.get(
                url,
                params=cast(Any, params),
                timeout=req_timeout,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                raise
            delay = backoff_delay(
                attempt,
                base_delay,
                cap_delay,
                rand=rand,
            )
            if not _wait(
                delay,
                deadline=deadline,
                monotonic=monotonic,
                sleep=sleep,
            ):
                msg = "shared indexer deadline exhausted during transport retry wait"
                raise IndexerDeadlineError(msg) from exc
            continue

        if should_fast_fail_response(response):
            return response

        if response.status_code not in RETRYABLE_STATUS_CODES:
            return response

        if attempt >= max_retries:
            return response

        retry_after_header = response.headers.get("retry-after")
        retry_after = parse_retry_after(retry_after_header, now=now)
        delay = backoff_delay(
            attempt,
            base_delay,
            cap_delay,
            retry_after=retry_after,
            rand=rand,
        )
        if not _wait(
            delay,
            deadline=deadline,
            monotonic=monotonic,
            sleep=sleep,
        ):
            msg = "shared indexer deadline exhausted during HTTP retry wait"
            raise IndexerDeadlineError(msg)

    if last_exc is not None:
        raise last_exc
    msg = "indexer_get exhausted retries without a response"
    raise RuntimeError(msg)

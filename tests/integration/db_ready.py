"""Monotonic-deadline PostgreSQL readiness polling for integration harness."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_INITIAL_BACKOFF_S = 0.05
DEFAULT_MAX_BACKOFF_S = 1.0


class DatabaseReadyTimeoutError(Exception):
    """Raised when readiness polling exhausts its deadline."""

    def __init__(self, last_error: Exception | None) -> None:
        self.last_error = last_error
        message = "PostgreSQL did not become ready before the deadline expired"
        if last_error is not None:
            message = f"{message}: {last_error}"
        super().__init__(message)


async def wait_for_database_ready(
    probe: Callable[[], Awaitable[None]],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S,
    max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Poll ``probe`` until success or ``timeout_s`` elapses.

    Uses capped exponential backoff between attempts (fast first try, no busy
    spin). The last connection error is preserved on timeout.
    """
    deadline = monotonic() + timeout_s
    backoff = initial_backoff_s
    last_error: Exception | None = None

    while monotonic() < deadline:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            async with asyncio.timeout(remaining):
                await probe()
        except Exception as exc:
            last_error = exc
        else:
            return

        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        await sleep(min(backoff, remaining, max_backoff_s))
        backoff = min(backoff * 2, max_backoff_s)

    raise DatabaseReadyTimeoutError(last_error)

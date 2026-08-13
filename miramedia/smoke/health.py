"""HTTP readiness polling for the smoke stack."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class SmokeStackReadyTimeoutError(Exception):
    """Raised when the smoke stack does not become ready in time."""

    def __init__(self, base_url: str, last_error: Exception | None) -> None:
        self.base_url = base_url
        self.last_error = last_error
        detail = f"Smoke stack at {base_url} did not become ready before the deadline expired"
        if last_error is not None:
            detail = f"{detail}: {last_error}"
        super().__init__(detail)


def _database_ready(health_url: str) -> bool:
    with urllib.request.urlopen(health_url, timeout=5) as response:  # noqa: S310
        if response.status != 200:
            msg = f"unexpected status {response.status}"
            raise RuntimeError(msg)
        body = json.loads(response.read().decode("utf-8"))
    db = body.get("db") if isinstance(body, dict) else None
    return isinstance(db, dict) and db.get("ok") is True


def wait_for_smoke_stack_ready(
    base_url: str,
    *,
    timeout_s: float = 120.0,
    initial_backoff_s: float = 0.1,
    max_backoff_s: float = 2.0,
) -> None:
    """Poll ``GET /api/v1/health`` until the database section reports healthy."""
    deadline = time.monotonic() + timeout_s
    backoff = initial_backoff_s
    last_error: Exception | None = None
    health_url = f"{base_url.rstrip('/')}/api/v1/health"

    while time.monotonic() < deadline:
        try:
            if _database_ready(health_url):
                return
            last_error = RuntimeError(
                "health endpoint reachable but database not ready"
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            RuntimeError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(backoff, remaining))
        backoff = min(backoff * 2, max_backoff_s)

    raise SmokeStackReadyTimeoutError(base_url, last_error)

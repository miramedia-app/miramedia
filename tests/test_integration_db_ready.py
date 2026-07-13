"""Unit tests for integration database readiness polling."""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.db_ready import (
    DatabaseReadyTimeoutError,
    wait_for_database_ready,
)


def _run(coro):
    return asyncio.run(coro)


def test_eventual_success_after_transient_connection_failures() -> None:
    attempts = {"n": 0}
    refused = ConnectionRefusedError()

    async def probe() -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise refused

    _run(
        wait_for_database_ready(
            probe,
            timeout_s=1.0,
            initial_backoff_s=0.01,
            max_backoff_s=0.05,
        )
    )
    assert attempts["n"] == 3


def test_timeout_preserves_last_connection_error() -> None:
    still_refused = ConnectionRefusedError("still refused")

    async def probe() -> None:
        raise still_refused

    with pytest.raises(DatabaseReadyTimeoutError) as exc_info:
        _run(
            wait_for_database_ready(
                probe,
                timeout_s=0.08,
                initial_backoff_s=0.02,
                max_backoff_s=0.02,
            )
        )

    assert exc_info.value.last_error is still_refused
    assert "still refused" in str(exc_info.value.last_error)


def test_polling_uses_capped_backoff_not_busy_spin() -> None:
    attempts = {"n": 0}
    sleep_delays: list[float] = []
    clock = {"now": 0.0}
    transient = OSError("transient")

    def monotonic() -> float:
        return clock["now"]

    async def probe() -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise transient

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        clock["now"] += delay

    _run(
        wait_for_database_ready(
            probe,
            timeout_s=1.0,
            initial_backoff_s=0.05,
            max_backoff_s=0.2,
            monotonic=monotonic,
            sleep=fake_sleep,
        )
    )

    assert attempts["n"] == 3
    assert sleep_delays == [0.05, 0.1]

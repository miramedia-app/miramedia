"""Postgres event-bridge retry characterization tests (DB-free).

Uses fake asyncpg connections to prove pending events survive reconnects.
Delivery is at-least-once within a live process — duplicate NOTIFY at the
ack boundary is tolerated because listeners re-fetch via REST.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from miramedia.events.bus import Event, EventBus

_PG_NOTIFY_FAILURE = "simulated pg_notify failure"


class _FakeConn:
    def __init__(self, *, fail_execute: bool = False) -> None:
        self.fail_execute = fail_execute
        self.execute_payloads: list[str] = []
        self.closed = False

    async def add_listener(self, _channel: str, _callback: object) -> None:
        return None

    async def execute(self, _query: str, _channel: str, payload: str) -> None:
        self.execute_payloads.append(payload)
        if self.fail_execute:
            raise RuntimeError(_PG_NOTIFY_FAILURE)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_bridge_retries_pending_event_after_execute_failure() -> None:
    bus = EventBus()
    bus._loop = asyncio.get_running_loop()
    bus._outbound = asyncio.Queue()

    event = Event(type="retry.test", data={"id": "1"})
    bus._outbound.put_nowait(event)
    expected_payload = bus._encode_notify_payload(event)

    connect_count = 0
    all_payloads: list[str] = []
    delivered = asyncio.Event()

    async def fake_connect(_dsn: str) -> _FakeConn:
        nonlocal connect_count
        connect_count += 1
        conn = _FakeConn(fail_execute=(connect_count == 1))

        async def execute(_query: str, _channel: str, payload: str) -> None:
            all_payloads.append(payload)
            if conn.fail_execute:
                raise RuntimeError(_PG_NOTIFY_FAILURE)
            delivered.set()

        conn.execute = execute  # type: ignore[method-assign]
        return conn

    async def fake_sleep(_delay: float) -> None:
        return None

    with (
        patch("asyncpg.connect", fake_connect),
        patch("asyncio.sleep", fake_sleep),
    ):
        task = asyncio.create_task(bus._run_postgres_bridge("fake-dsn", "ch"))
        try:
            await asyncio.wait_for(delivered.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert connect_count == 2
    assert len(all_payloads) == 2
    assert all_payloads[0] == expected_payload
    assert all_payloads[1] == expected_payload
    assert bus._outbound.empty()


@pytest.mark.anyio
async def test_bridge_clears_pending_and_delivers_next_event() -> None:
    bus = EventBus()
    bus._loop = asyncio.get_running_loop()
    bus._outbound = asyncio.Queue()

    first = Event(type="first.event", data={"n": 1})
    second = Event(type="second.event", data={"n": 2})
    bus._outbound.put_nowait(first)
    bus._outbound.put_nowait(second)

    delivered_types: list[str] = []
    both_delivered = asyncio.Event()

    async def fake_connect(_dsn: str) -> _FakeConn:
        conn = _FakeConn()

        async def execute(_query: str, _channel: str, payload: str) -> None:
            parsed = json.loads(payload)
            delivered_types.append(parsed["type"])
            if len(delivered_types) == 2:
                both_delivered.set()

        conn.execute = execute  # type: ignore[method-assign]
        return conn

    with patch("asyncpg.connect", fake_connect):
        task = asyncio.create_task(bus._run_postgres_bridge("fake-dsn", "ch"))
        try:
            await asyncio.wait_for(both_delivered.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert delivered_types == ["first.event", "second.event"]
    assert bus._outbound.empty()


@pytest.mark.anyio
async def test_bridge_cancellation_exits_without_reconnect_loop() -> None:
    bus = EventBus()
    bus._loop = asyncio.get_running_loop()
    bus._outbound = asyncio.Queue()

    event = Event(type="cancel.test", data={})
    bus._outbound.put_nowait(event)

    connect_count = 0
    sleep_count = 0
    blocked_on_sleep = asyncio.Event()
    original_sleep = asyncio.sleep

    async def fake_connect(_dsn: str) -> _FakeConn:
        nonlocal connect_count
        connect_count += 1
        return _FakeConn(fail_execute=True)

    async def fake_sleep(_delay: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        blocked_on_sleep.set()
        await original_sleep(3600)

    with (
        patch("asyncpg.connect", fake_connect),
        patch("asyncio.sleep", fake_sleep),
    ):
        task = asyncio.create_task(bus._run_postgres_bridge("fake-dsn", "ch"))
        await asyncio.wait_for(blocked_on_sleep.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert connect_count == 1
    assert sleep_count == 1

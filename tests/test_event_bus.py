"""EventBus publish/delivery characterization tests.

Oversize NOTIFY payloads: when the full JSON exceeds 7000 bytes,
``_encode_notify_payload`` drops ``data`` and returns a trimmed envelope
with an empty ``data`` dict so listeners still invalidate via type alone.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from miramedia.events.bus import Event, EventBus


@pytest.mark.anyio
async def test_on_loop_publish_fan_out() -> None:
    bus = EventBus()
    _sub1, q1 = await bus.subscribe()
    _sub2, q2 = await bus.subscribe()

    event = Event(type="test.fanout", data={"key": "value"})
    bus.publish(event)

    assert q1.get_nowait() == event
    assert q2.get_nowait() == event


@pytest.mark.anyio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    _keep_id, q_keep = await bus.subscribe()
    drop_id, q_drop = await bus.subscribe()

    await bus.unsubscribe(drop_id)

    event = Event(type="test.unsub", data={})
    bus.publish(event)

    assert q_keep.get_nowait() == event
    with pytest.raises(asyncio.QueueEmpty):
        q_drop.get_nowait()


@pytest.mark.anyio
async def test_drop_on_full_does_not_raise() -> None:
    bus = EventBus(queue_maxsize=2)
    _full_id, q_full = await bus.subscribe()
    _ok_id, q_ok = await bus.subscribe()

    for i in range(2):
        bus.publish(Event(type="fill", data={"i": i}))
        q_ok.get_nowait()

    bus.publish(Event(type="overflow"))

    assert q_ok.get_nowait().type == "overflow"
    assert q_full.full()


@pytest.mark.anyio
async def test_cross_thread_publish_wakes_consumer() -> None:
    bus = EventBus()
    _sub_id, q = await bus.subscribe()

    event = Event(type="test.cross_thread", data={"id": "1"})
    thread = threading.Thread(target=bus.publish, args=(event,))
    thread.start()

    received = await asyncio.wait_for(q.get(), 2)
    thread.join()

    assert received == event


def test_publish_with_no_subscribers_is_noop() -> None:
    bus = EventBus()
    assert bus._outbound is None

    bus.publish(Event(type="test.noop", data={}))

    assert bus._outbound is None


@pytest.mark.anyio
async def test_publish_cross_process_drop_on_full() -> None:
    bus = EventBus()
    await bus.subscribe()

    bus._outbound = asyncio.Queue(maxsize=1)
    prefill = Event(type="prefill")
    bus._outbound.put_nowait(prefill)

    bus.publish(Event(type="dropped"))

    assert bus._outbound.qsize() == 1
    assert bus._outbound.get_nowait() == prefill

    bus._outbound = None
    bus.publish(Event(type="no.bridge"))


def test_encode_notify_payload_under_byte_cap() -> None:
    bus = EventBus()
    event = Event(type="small.event", data={"id": "1"})

    payload = bus._encode_notify_payload(event)

    assert len(payload.encode("utf-8")) <= EventBus._NOTIFY_MAX_BYTES
    parsed = json.loads(payload)
    assert parsed["type"] == "small.event"
    assert parsed["data"] == {"id": "1"}


def test_encode_notify_payload_over_byte_cap() -> None:
    bus = EventBus()
    event = Event(type="big.event", data={"blob": "x" * 8000})

    payload = bus._encode_notify_payload(event)

    assert len(payload.encode("utf-8")) <= EventBus._NOTIFY_MAX_BYTES
    parsed = json.loads(payload)
    assert parsed["type"] == "big.event"
    assert parsed["data"] == {}

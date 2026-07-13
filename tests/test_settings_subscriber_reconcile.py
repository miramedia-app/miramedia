"""Subscriber reconciliation and missed-notification recovery tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from miramedia.events.bus import Event
from miramedia.settings.reload import (
    RECONCILE_INTERVAL_SECONDS,
    get_local_committed_revision,
    reconcile_settings_revision_from_db,
    reset_settings_reload_state_for_tests,
    reset_settings_subscriber_for_tests,
    set_local_committed_revision,
)
from miramedia.settings.service import apply_live_config_from_overrides


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_settings_reload_state_for_tests()
    reset_settings_subscriber_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_settings_reload_state_for_tests()
    reset_settings_subscriber_for_tests()
    apply_live_config_from_overrides({})


def test_reconcile_loads_newer_db_revision() -> None:
    from miramedia.config import MiraMediaConfig

    async def _fetch() -> tuple[dict, int]:
        return {"misc": {"development": True}}, 3

    async def _run() -> None:
        set_local_committed_revision(1)
        await reconcile_settings_revision_from_db(_fetch)
        assert MiraMediaConfig().misc.development is True
        assert get_local_committed_revision() == 3

    asyncio.run(_run())


def test_reconcile_ignores_stale_db_revision() -> None:
    from miramedia.config import MiraMediaConfig

    async def _fetch() -> tuple[dict, int]:
        return {"misc": {"development": True}}, 1

    async def _run() -> None:
        set_local_committed_revision(2)
        await reconcile_settings_revision_from_db(_fetch)
        assert MiraMediaConfig().misc.development is False
        assert get_local_committed_revision() == 2

    asyncio.run(_run())


def test_subscriber_loop_reconciles_on_queue_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings import reload as reload_mod

    reconcile = AsyncMock()
    monkeypatch.setattr(reload_mod, "reconcile_settings_revision_from_db", reconcile)
    monkeypatch.setattr(reload_mod, "RECONCILE_INTERVAL_SECONDS", 0.01)

    queue: asyncio.Queue[Event] = asyncio.Queue()

    async def _run() -> None:
        reload_mod._subscriber_queue = queue
        task = asyncio.create_task(reload_mod._subscriber_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert reconcile.await_count >= 1

    asyncio.run(_run())


def test_reconcile_interval_is_bounded() -> None:
    assert RECONCILE_INTERVAL_SECONDS <= 60


def test_subscriber_reconciles_despite_unrelated_event_flood(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings import reload as reload_mod

    reconcile = AsyncMock()
    monkeypatch.setattr(reload_mod, "reconcile_settings_revision_from_db", reconcile)
    monkeypatch.setattr(reload_mod, "RECONCILE_INTERVAL_SECONDS", 0.05)

    queue: asyncio.Queue[Event] = asyncio.Queue()

    async def _run() -> None:
        reload_mod._subscriber_queue = queue
        task = asyncio.create_task(reload_mod._subscriber_loop())
        for _ in range(20):
            await queue.put(Event(type="unrelated.noise", data={}))
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert reconcile.await_count >= 1

    asyncio.run(_run())


def test_subscriber_restart_cleans_dead_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings import reload as reload_mod

    monkeypatch.setattr(reload_mod, "_subscriber_loop", AsyncMock())

    async def _run() -> None:
        first = await reload_mod.start_settings_revision_subscriber()
        first_sub_id = reload_mod._subscriber_sub_id
        assert first_sub_id is not None
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass

        second = await reload_mod.start_settings_revision_subscriber()
        assert second is not first
        assert reload_mod._subscriber_sub_id is not None
        assert reload_mod._subscriber_sub_id != first_sub_id
        await reload_mod.stop_settings_revision_subscriber()
        assert reload_mod._subscriber_sub_id is None

    asyncio.run(_run())

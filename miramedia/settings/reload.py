"""Cross-worker settings reload coordination."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    commit_auth_runtime_generation,
    prepare_auth_runtime_for_overrides,
)
from miramedia.events.bus import Event, get_event_bus
from miramedia.settings.service import apply_live_config_from_overrides

log = logging.getLogger(__name__)

SETTINGS_REVISION_EVENT = "settings.revision.changed"
RECONCILE_INTERVAL_SECONDS = 30.0

_local_committed_revision = 0
_reload_lock = asyncio.Lock()
_subscriber_task: asyncio.Task[None] | None = None
_subscriber_sub_id: str | None = None
_subscriber_queue: asyncio.Queue[Event] | None = None


def get_local_committed_revision() -> int:
    return _local_committed_revision


def publish_settings_revision_changed(revision: int) -> None:
    get_event_bus().publish(
        Event(type=SETTINGS_REVISION_EVENT, data={"revision": revision})
    )


def _apply_live_mutation_critical_section(
    merged_overrides: dict,
    prospective: AuthRuntimeGeneration,
) -> None:
    apply_live_config_from_overrides(merged_overrides)
    commit_auth_runtime_generation(prospective)


async def reload_committed_settings(
    overrides: dict,
    *,
    revision: int,
) -> None:
    """Validate, stage, and atomically apply a committed DB settings snapshot."""
    global _local_committed_revision

    if revision <= _local_committed_revision:
        return

    prospective = await prepare_auth_runtime_for_overrides(overrides)
    async with _reload_lock:
        if revision <= _local_committed_revision:
            return
        _apply_live_mutation_critical_section(overrides, prospective)
        _local_committed_revision = revision
        log.info("Reloaded committed settings revision %s", revision)


async def reconcile_settings_revision_from_db(
    fetch: Callable[[], Awaitable[tuple[dict, int]]],
) -> None:
    """Load current DB revision and converge if newer than local."""
    overrides, revision = await fetch()
    if revision <= _local_committed_revision:
        return
    await reload_committed_settings(overrides, revision=revision)


async def handle_settings_revision_event(event: Event) -> None:
    revision = int(event.data.get("revision", 0))
    if revision <= _local_committed_revision:
        return
    from miramedia.database import SessionLocalBackground
    from miramedia.settings.repository import SettingsRepository

    assert SessionLocalBackground is not None  # noqa: S101
    async with SessionLocalBackground() as db:
        overrides, current_revision = await SettingsRepository(
            db
        ).get_overrides_with_revision()
    if current_revision < revision:
        log.warning(
            "Ignoring settings revision notification %s; DB is at %s",
            revision,
            current_revision,
        )
        return
    await reload_committed_settings(overrides, revision=current_revision)


async def bootstrap_settings_revision_from_db() -> None:
    from miramedia.database import SessionLocalBackground
    from miramedia.settings.repository import SettingsRepository

    assert SessionLocalBackground is not None  # noqa: S101
    async with SessionLocalBackground() as db:
        overrides, revision = await SettingsRepository(db).get_overrides_with_revision()
    if revision > _local_committed_revision:
        await reload_committed_settings(overrides, revision=revision)


def set_local_committed_revision(revision: int) -> None:
    global _local_committed_revision
    _local_committed_revision = revision


def reset_settings_reload_state_for_tests() -> None:
    global _local_committed_revision
    _local_committed_revision = 0


async def _subscriber_loop() -> None:
    assert _subscriber_queue is not None  # noqa: S101
    while True:
        try:
            event = await asyncio.wait_for(
                _subscriber_queue.get(),
                timeout=RECONCILE_INTERVAL_SECONDS,
            )
        except TimeoutError:
            try:
                await reconcile_settings_revision_from_db(_fetch_db_revision)
            except Exception:
                log.exception("Periodic settings revision reconciliation failed")
            continue
        if event.type != SETTINGS_REVISION_EVENT:
            continue
        try:
            await handle_settings_revision_event(event)
        except Exception:
            log.exception("Failed to reload settings from revision event")


async def _fetch_db_revision() -> tuple[dict, int]:
    from miramedia.database import SessionLocalBackground
    from miramedia.settings.repository import SettingsRepository

    assert SessionLocalBackground is not None  # noqa: S101
    async with SessionLocalBackground() as db:
        return await SettingsRepository(db).get_overrides_with_revision()


async def start_settings_revision_subscriber() -> asyncio.Task[None]:
    """Start the revision subscriber once per process; idempotent."""
    global _subscriber_task, _subscriber_sub_id, _subscriber_queue

    if _subscriber_task is not None and not _subscriber_task.done():
        return _subscriber_task

    bus = get_event_bus()
    _subscriber_sub_id, _subscriber_queue = await bus.subscribe()
    _subscriber_task = asyncio.create_task(_subscriber_loop())
    return _subscriber_task


async def stop_settings_revision_subscriber() -> None:
    """Cancel subscriber and release bus subscription."""
    global _subscriber_task, _subscriber_sub_id, _subscriber_queue

    if _subscriber_task is not None and not _subscriber_task.done():
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass
    _subscriber_task = None

    if _subscriber_sub_id is not None:
        await get_event_bus().unsubscribe(_subscriber_sub_id)
    _subscriber_sub_id = None
    _subscriber_queue = None


def reset_settings_subscriber_for_tests() -> None:
    """Reset subscriber globals without awaiting (tests only)."""
    global _subscriber_task, _subscriber_sub_id, _subscriber_queue
    if _subscriber_task is not None and not _subscriber_task.done():
        _subscriber_task.cancel()
    _subscriber_task = None
    _subscriber_sub_id = None
    _subscriber_queue = None

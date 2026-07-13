"""Cross-worker settings reload coordination."""

from __future__ import annotations

import asyncio
import logging

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    commit_auth_runtime_generation,
    prepare_auth_runtime_for_overrides,
)
from miramedia.events.bus import Event, get_event_bus
from miramedia.settings.service import apply_live_config_from_overrides

log = logging.getLogger(__name__)

SETTINGS_REVISION_EVENT = "settings.revision.changed"

_local_committed_revision = 0
_reload_lock = asyncio.Lock()


def get_local_committed_revision() -> int:
    return _local_committed_revision


def publish_settings_revision_changed(revision: int) -> None:
    get_event_bus().publish(
        Event(type=SETTINGS_REVISION_EVENT, data={"revision": revision})
    )


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


def _apply_live_mutation_critical_section(
    merged_overrides: dict,
    prospective: AuthRuntimeGeneration,
) -> None:
    apply_live_config_from_overrides(merged_overrides)
    commit_auth_runtime_generation(prospective)


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


async def start_settings_revision_subscriber() -> asyncio.Task[None]:
    bus = get_event_bus()
    sub_id, queue = await bus.subscribe()

    async def _run() -> None:
        try:
            while True:
                event = await queue.get()
                if event.type != SETTINGS_REVISION_EVENT:
                    continue
                try:
                    await handle_settings_revision_event(event)
                except Exception:
                    log.exception("Failed to reload settings from revision event")
        finally:
            await bus.unsubscribe(sub_id)

    import asyncio

    task = asyncio.create_task(_run())

    def _discard(_task: asyncio.Task[None]) -> None:
        _task.cancel()

    task.add_done_callback(_discard)
    return task

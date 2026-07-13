"""Startup partial-failure teardown tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import miramedia.settings.reload as settings_reload
from miramedia.settings.reload import (
    _subscriber_sub_id,
    _subscriber_task,
    reset_settings_reload_state_for_tests,
    reset_settings_subscriber_for_tests,
)


def _mock_db_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    return session


@asynccontextmanager
async def _patched_persistence(*, fail_after_subscriber: bool) -> Any:
    """Run start_persistence with DB/admin steps mocked for unit isolation."""
    reset_settings_reload_state_for_tests()
    reset_settings_subscriber_for_tests()

    session = _mock_db_session()
    with (
        patch("miramedia.database.init_engine"),
        patch("miramedia.logging.attach_db_handler"),
        patch("miramedia.database.SessionLocalBackground", return_value=session),
        patch("miramedia.database.get_engine") as mock_engine,
        patch("miramedia.indexers.seed.seed_preloaded_sites", new_callable=AsyncMock),
        patch("miramedia.torrents.repository.TorrentRepository") as mock_torrent_repo,
        patch("miramedia.settings.repository.SettingsRepository") as mock_settings_repo,
        patch(
            "miramedia.shows.cleanup.cleanup_stale_show_preferences",
            new_callable=AsyncMock,
        ),
        patch(
            "miramedia.movies.cleanup.cleanup_stale_movie_preferences",
            new_callable=AsyncMock,
        ),
        patch("miramedia.auth.runtime.initialize_auth_runtime", new_callable=AsyncMock),
        patch(
            "miramedia.auth.users.migrate_admin_emails_to_superuser_flag",
            new_callable=AsyncMock,
        ),
        patch("miramedia.logging.apply_development_log_level"),
        patch(
            "miramedia.auth.users.create_default_admin_user",
            new_callable=AsyncMock,
            side_effect=RuntimeError("admin bootstrap failed")
            if fail_after_subscriber
            else None,
        ),
    ):
        eng = MagicMock()
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        eng.connect = MagicMock(return_value=conn)
        mock_engine.return_value = eng
        mock_torrent_repo.return_value.delete_orphaned_torrents = AsyncMock()
        mock_settings_repo.return_value.get_overrides_with_revision = AsyncMock(
            return_value=({}, 0)
        )
        yield


def test_lifespan_shutdown_runs_when_persistence_fails_after_subscriber() -> None:
    stop_calls = 0
    original_stop = settings_reload.stop_settings_revision_subscriber

    async def counting_stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        await original_stop()

    async def run() -> None:
        from miramedia.main import app, lifespan

        with (
            patch.object(
                settings_reload,
                "stop_settings_revision_subscriber",
                counting_stop,
            ),
            patch.dict("os.environ", {"MIRAMEDIA_SCHEDULER_DISABLED": "true"}),
        ):
            async with _patched_persistence(fail_after_subscriber=True):
                gen = lifespan(app)
                with pytest.raises(RuntimeError, match="admin bootstrap failed"):
                    await gen.__aenter__()
                await gen.__aexit__(None, None, None)

        assert stop_calls == 1
        assert _subscriber_task is None
        assert _subscriber_sub_id is None

    asyncio.run(run())


def test_two_lifespan_cycles_each_shutdown_subscriber() -> None:
    stop_calls = 0
    original_stop = settings_reload.stop_settings_revision_subscriber

    async def counting_stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        await original_stop()

    async def run() -> None:
        from miramedia.main import app, lifespan

        with (
            patch.object(
                settings_reload,
                "stop_settings_revision_subscriber",
                counting_stop,
            ),
            patch.dict("os.environ", {"MIRAMEDIA_SCHEDULER_DISABLED": "true"}),
        ):
            async with _patched_persistence(fail_after_subscriber=False):
                gen = lifespan(app)
                await gen.__aenter__()
                await gen.__aexit__(None, None, None)
            async with _patched_persistence(fail_after_subscriber=False):
                gen = lifespan(app)
                await gen.__aenter__()
                await gen.__aexit__(None, None, None)

        assert stop_calls == 2

    asyncio.run(run())


def test_lifespan_shutdown_stops_event_bridge_when_persistence_raises() -> None:
    bridge_stops = 0
    bridge_started = False

    async def _start_bridge(_dsn: str) -> None:
        nonlocal bridge_started
        bridge_started = True

    async def _stop_bridge() -> None:
        nonlocal bridge_stops
        bridge_stops += 1

    async def run() -> None:
        from miramedia.events.bus import EventBus
        from miramedia.main import app, lifespan

        bus = EventBus()
        with (
            patch("miramedia.events.bus.get_event_bus", return_value=bus),
            patch.object(bus, "start_postgres_bridge", side_effect=_start_bridge),
            patch.object(bus, "stop_postgres_bridge", side_effect=_stop_bridge),
            patch.dict("os.environ", {"MIRAMEDIA_SCHEDULER_DISABLED": "true"}),
        ):
            async with _patched_persistence(fail_after_subscriber=True):
                gen = lifespan(app)
                with pytest.raises(RuntimeError, match="admin bootstrap failed"):
                    await gen.__aenter__()
                await gen.__aexit__(None, None, None)

        assert bridge_started is True
        assert bridge_stops == 1

    asyncio.run(run())

"""Characterization tests for the optional library watcher polling loop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from miramedia.library_watcher import run_library_watcher


def _run(coro):
    return asyncio.run(coro)


def test_run_library_watcher_no_roots_returns_without_refresh(monkeypatch) -> None:
    config = MagicMock()
    config.misc.movie_libraries = []
    config.misc.show_libraries = []
    monkeypatch.setattr("miramedia.config.MiraMediaConfig", lambda: config)

    refresh_called = False

    async def _refresh_media_state(_db) -> None:
        nonlocal refresh_called
        refresh_called = True

    monkeypatch.setattr(
        "miramedia.media_state.refresh_media_state", _refresh_media_state
    )

    _run(run_library_watcher())

    assert refresh_called is False


def test_run_library_watcher_one_iteration_refreshes_and_invalidates(
    monkeypatch,
) -> None:
    lib = MagicMock()
    lib.path = "/movies"
    config = MagicMock()
    config.misc.movie_libraries = [lib]
    config.misc.show_libraries = []
    monkeypatch.setattr("miramedia.config.MiraMediaConfig", lambda: config)

    calls = {"refresh": 0, "commit": 0, "invalidate": 0}

    class _Db:
        async def commit(self) -> None:
            calls["commit"] += 1

    @asynccontextmanager
    async def _session_local_background():
        yield _Db()

    async def _refresh_media_state(_db) -> None:
        calls["refresh"] += 1

    def _invalidate_disk_scan_cache() -> None:
        calls["invalidate"] += 1

    sleep_calls = 0

    async def _sleep(_interval: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground", _session_local_background
    )
    monkeypatch.setattr(
        "miramedia.media_state.refresh_media_state", _refresh_media_state
    )
    monkeypatch.setattr(
        "miramedia.disk_scan.invalidate_disk_scan_cache",
        _invalidate_disk_scan_cache,
    )
    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        _run(run_library_watcher())

    assert calls == {"refresh": 1, "commit": 1, "invalidate": 1}


def test_run_library_watcher_refresh_exception_is_logged_not_raised(
    monkeypatch,
    caplog,
) -> None:
    lib = MagicMock()
    lib.path = "/shows"
    config = MagicMock()
    config.misc.movie_libraries = []
    config.misc.show_libraries = [lib]
    monkeypatch.setattr("miramedia.config.MiraMediaConfig", lambda: config)

    @asynccontextmanager
    async def _session_local_background():
        yield MagicMock()

    async def _refresh_media_state(_db) -> None:
        msg = "refresh failed"
        raise RuntimeError(msg)

    sleep_calls = 0

    async def _sleep(_interval: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground", _session_local_background
    )
    monkeypatch.setattr(
        "miramedia.media_state.refresh_media_state", _refresh_media_state
    )
    monkeypatch.setattr("miramedia.disk_scan.invalidate_disk_scan_cache", lambda: None)
    monkeypatch.setattr(asyncio, "sleep", _sleep)

    with caplog.at_level("ERROR"), pytest.raises(asyncio.CancelledError):
        _run(run_library_watcher())

    assert any(
        "Library watcher refresh failed" in record.message for record in caplog.records
    )

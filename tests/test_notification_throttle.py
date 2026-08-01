"""Tests for notification send throttling and provider-cache behavior."""

from __future__ import annotations

import threading

import pytest

from miramedia.notifications.manager import NotificationManager
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)
from miramedia.settings.reload import set_local_committed_revision


class _RecordingProvider(AbstractNotificationServiceProvider):
    def __init__(self) -> None:
        self.calls: list[MessageNotification] = []

    def send_notification(self, message: MessageNotification) -> bool:
        self.calls.append(message)
        return True


def _manager_with_recording_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[NotificationManager, _RecordingProvider]:
    provider = _RecordingProvider()
    build_calls: list[int] = []
    revision = {"value": 0}

    def fake_build(
        _self: NotificationManager,
    ) -> list[AbstractNotificationServiceProvider]:
        build_calls.append(revision["value"])
        return [provider]

    monkeypatch.setattr(NotificationManager, "_build_providers_uncached", fake_build)
    manager = NotificationManager()
    manager._provider_build_calls = build_calls  # type: ignore[attr-defined]
    manager._test_revision = revision  # type: ignore[attr-defined]
    return manager, provider


def test_first_send_goes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _manager_with_recording_provider(monkeypatch)
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])

    manager.send_notification("TMDB API Error", "first failure")

    assert len(provider.calls) == 1
    assert provider.calls[0].message == "first failure"


def test_second_same_title_and_message_within_window_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _manager_with_recording_provider(monkeypatch)
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])

    manager.send_notification("TMDB API Error", "same failure")
    manager.send_notification("TMDB API Error", "same failure")

    assert len(provider.calls) == 1
    assert provider.calls[0].message == "same failure"


def test_same_title_different_messages_not_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _manager_with_recording_provider(monkeypatch)
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])

    manager.send_notification("Missing Episode File", "Show A S01E01")
    manager.send_notification("Missing Episode File", "Show B S02E03")

    assert len(provider.calls) == 2
    assert provider.calls[0].message == "Show A S01E01"
    assert provider.calls[1].message == "Show B S02E03"


def test_different_titles_do_not_suppress_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _manager_with_recording_provider(monkeypatch)
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])

    manager.send_notification("TMDB API Error", "tmdb down")
    manager.send_notification("Indexer Error", "indexer down")

    assert len(provider.calls) == 2
    assert provider.calls[0].title == "TMDB API Error"
    assert provider.calls[1].title == "Indexer Error"


def test_after_window_next_send_includes_suppressed_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _manager_with_recording_provider(monkeypatch)
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])
    monkeypatch.setattr("miramedia.notifications.manager._SUPPRESS_WINDOW_S", 900.0)

    manager.send_notification("TMDB API Error", "first")
    now["t"] = 1001.0
    manager.send_notification("TMDB API Error", "first")
    now["t"] = 1002.0
    manager.send_notification("TMDB API Error", "first")

    now["t"] = 2000.0  # past 900s window
    manager.send_notification("TMDB API Error", "first")

    assert len(provider.calls) == 2
    assert provider.calls[1].message == (
        "first (+2 similar suppressed in the last 15 min)"
    )


def test_zero_providers_no_crash_no_state_pollution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = NotificationManager()
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])
    monkeypatch.setattr(
        NotificationManager,
        "_build_providers_uncached",
        lambda _self: [],
    )

    manager.send_notification("TMDB API Error", "no providers")
    manager.send_notification("TMDB API Error", "still no providers")

    assert manager._recent_sends == {}
    assert manager._suppressed_counts == {}


def test_provider_cache_reused_at_same_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    uncached_calls = 0

    def uncached(
        _self: NotificationManager,
    ) -> list[AbstractNotificationServiceProvider]:
        nonlocal uncached_calls
        uncached_calls += 1
        return [provider]

    monkeypatch.setattr(NotificationManager, "_build_providers_uncached", uncached)
    set_local_committed_revision(1)
    manager = NotificationManager()

    manager.send_notification("Title A", "one")
    manager.send_notification("Title B", "two")

    assert uncached_calls == 1
    assert len(provider.calls) == 2


def test_provider_cache_rebuilt_after_revision_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    uncached_calls = 0

    def uncached(
        _self: NotificationManager,
    ) -> list[AbstractNotificationServiceProvider]:
        nonlocal uncached_calls
        uncached_calls += 1
        return [provider]

    monkeypatch.setattr(NotificationManager, "_build_providers_uncached", uncached)
    set_local_committed_revision(1)
    manager = NotificationManager()

    manager.send_notification("Title One", "first")
    set_local_committed_revision(2)
    manager.send_notification("Title Two", "second")

    assert uncached_calls == 2


def test_concurrent_same_title_sends_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(
        NotificationManager,
        "_build_providers_uncached",
        lambda _self: [provider],
    )
    manager = NotificationManager()
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            manager.send_notification("TMDB API Error", "concurrent")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(provider.calls) == 1


def test_prune_evicts_stale_tuple_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, provider = _manager_with_recording_provider(monkeypatch)
    now = {"t": 1000.0}
    monkeypatch.setattr("miramedia.notifications.manager.monotonic", lambda: now["t"])
    monkeypatch.setattr("miramedia.notifications.manager._SUPPRESS_WINDOW_S", 900.0)
    monkeypatch.setattr("miramedia.notifications.manager._MAX_RECENT_SENDS", 2)

    manager.send_notification("Title A", "message a")
    now["t"] = 1001.0
    manager.send_notification("Title B", "message b")

    now["t"] = 2000.0  # past window — A and B are stale
    manager.send_notification("Title C", "message c")

    assert ("Title A", "message a") not in manager._recent_sends
    assert ("Title B", "message b") not in manager._recent_sends
    assert ("Title C", "message c") in manager._recent_sends
    assert len(provider.calls) == 3

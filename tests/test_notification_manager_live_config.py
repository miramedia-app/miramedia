"""Tests that NotificationManager rebuilds providers when settings revision changes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miramedia.notifications.manager import NotificationManager
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.gotify import (
    GotifyNotificationServiceProvider,
)
from miramedia.notifications.service_providers.ntfy import (
    NtfyNotificationServiceProvider,
)
from miramedia.settings.reload import set_local_committed_revision


def _patch_notifications_config(
    monkeypatch: pytest.MonkeyPatch,
    notifications: SimpleNamespace,
) -> None:
    def config_factory() -> SimpleNamespace:
        return SimpleNamespace(notifications=notifications)

    monkeypatch.setattr(
        "miramedia.notifications.manager.MiraMediaConfig",
        config_factory,
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.gotify.MiraMediaConfig",
        config_factory,
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.ntfy.MiraMediaConfig",
        config_factory,
    )


@pytest.fixture
def notifications_config(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    revision = {"value": 0}

    def bump_revision() -> None:
        revision["value"] += 1
        set_local_committed_revision(revision["value"])

    notifications = SimpleNamespace(
        subject_prefix="",
        email_notifications=SimpleNamespace(enabled=False, emails=[]),
        gotify=SimpleNamespace(
            enabled=False,
            api_key="test-token",
            url="https://gotify.example.test",
        ),
        ntfy=SimpleNamespace(
            enabled=False,
            url="https://ntfy.example.test/my-topic",
        ),
        pushover=SimpleNamespace(enabled=False, api_key=None, user=None),
    )
    _patch_notifications_config(monkeypatch, notifications)
    notifications.bump_revision = bump_revision  # type: ignore[attr-defined]
    set_local_committed_revision(0)
    return notifications


def test_provider_appears_after_config_enabled(
    notifications_config: SimpleNamespace,
) -> None:
    manager = NotificationManager()
    assert manager.is_configured() is False

    notifications_config.gotify.enabled = True
    notifications_config.bump_revision()

    assert manager.is_configured() is True
    assert manager.get_configured_providers() == ["GotifyNotificationServiceProvider"]


def test_provider_disappears_after_config_disabled(
    notifications_config: SimpleNamespace,
) -> None:
    notifications_config.gotify.enabled = True
    manager = NotificationManager()
    assert manager.is_configured() is True

    notifications_config.gotify.enabled = False
    notifications_config.bump_revision()

    assert manager.is_configured() is False
    assert manager.get_configured_providers() == []


def test_send_notification_uses_freshly_built_provider(
    monkeypatch: pytest.MonkeyPatch,
    notifications_config: SimpleNamespace,
) -> None:
    calls: list[MessageNotification] = []

    def record_send(
        _self: GotifyNotificationServiceProvider,
        message: MessageNotification,
    ) -> bool:
        calls.append(message)
        return True

    monkeypatch.setattr(
        GotifyNotificationServiceProvider,
        "send_notification",
        record_send,
    )

    manager = NotificationManager()
    assert manager.is_configured() is False

    notifications_config.gotify.enabled = True
    notifications_config.bump_revision()
    manager.send_notification("Title", "Body")

    assert len(calls) == 1
    assert calls[0].title == "Title"
    assert calls[0].message == "Body"


def test_constructor_failure_of_one_provider_does_not_block_send(
    monkeypatch: pytest.MonkeyPatch,
    notifications_config: SimpleNamespace,
) -> None:
    ntfy_calls: list[MessageNotification] = []

    def boom_init(_self: GotifyNotificationServiceProvider) -> None:
        msg = "Gotify init failed"
        raise RuntimeError(msg)

    def record_ntfy_send(
        _self: NtfyNotificationServiceProvider,
        message: MessageNotification,
    ) -> bool:
        ntfy_calls.append(message)
        return True

    monkeypatch.setattr(GotifyNotificationServiceProvider, "__init__", boom_init)
    monkeypatch.setattr(
        NtfyNotificationServiceProvider, "send_notification", record_ntfy_send
    )

    notifications_config.gotify.enabled = True
    notifications_config.ntfy.enabled = True
    manager = NotificationManager()

    manager.send_notification("Title", "Body")

    assert len(ntfy_calls) == 1
    assert ntfy_calls[0].title == "Title"
    assert ntfy_calls[0].message == "Body"

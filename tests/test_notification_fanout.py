"""Characterization tests for notification fan-out and HTTP provider payloads."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from miramedia.notifications.manager import NotificationManager
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)
from miramedia.notifications.service_providers.gotify import (
    GotifyNotificationServiceProvider,
)
from miramedia.notifications.service_providers.ntfy import (
    NtfyNotificationServiceProvider,
)
from miramedia.notifications.service_providers.pushover import (
    PushoverNotificationServiceProvider,
)


class _RecordingProvider(AbstractNotificationServiceProvider):
    def __init__(
        self,
        *,
        name: str,
        behavior: str = "ok",
    ) -> None:
        self.name = name
        self.behavior = behavior
        self.calls: list[MessageNotification] = []

    def send_notification(self, message: MessageNotification) -> bool:
        self.calls.append(message)
        if self.behavior == "raise":
            err = f"{self.name} failed"
            raise RuntimeError(err)
        if self.behavior == "false":
            return False
        return True


def _manager_with_providers(
    providers: list[AbstractNotificationServiceProvider],
) -> NotificationManager:
    manager = NotificationManager.__new__(NotificationManager)
    manager.providers = providers
    return manager


def test_failing_provider_does_not_block_others() -> None:
    first = _RecordingProvider(name="first", behavior="raise")
    second = _RecordingProvider(name="second", behavior="false")
    third = _RecordingProvider(name="third", behavior="ok")
    manager = _manager_with_providers([first, second, third])

    manager.send_notification("Title", "Body")

    assert len(first.calls) == 1
    assert len(second.calls) == 1
    assert len(third.calls) == 1


def test_subject_prefix_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _RecordingProvider(name="only")
    manager = _manager_with_providers([provider])
    monkeypatch.setattr(
        "miramedia.notifications.manager.MiraMediaConfig",
        lambda: SimpleNamespace(
            notifications=SimpleNamespace(subject_prefix="[MM]"),
        ),
    )

    manager.send_notification("Download done", "Episode imported")

    assert len(provider.calls) == 1
    assert provider.calls[0].title == "[MM] Download done"
    assert provider.calls[0].message == "Episode imported"


def _fake_response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


def test_gotify_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _fake_response(200)

    monkeypatch.setattr(
        "miramedia.notifications.service_providers.gotify.MiraMediaConfig",
        lambda: SimpleNamespace(
            notifications=SimpleNamespace(
                gotify=SimpleNamespace(
                    enabled=True,
                    api_key="test-token",
                    url="https://gotify.example.test",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.gotify.requests.post",
        fake_post,
    )

    provider = GotifyNotificationServiceProvider()
    ok = provider.send_notification(
        MessageNotification(title="Gotify Title", message="Gotify body"),
    )
    assert ok is True
    assert captured["url"] == "https://gotify.example.test/message?token=test-token"
    assert captured["json"] == {
        "message": "Gotify body",
        "title": "Gotify Title",
    }
    assert captured["timeout"] == 60

    # Error status returns False
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.gotify.requests.post",
        lambda **_kwargs: _fake_response(500),
    )
    assert (
        provider.send_notification(
            MessageNotification(title="x", message="y"),
        )
        is False
    )


def test_ntfy_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _fake_response(201)

    monkeypatch.setattr(
        "miramedia.notifications.service_providers.ntfy.MiraMediaConfig",
        lambda: SimpleNamespace(
            notifications=SimpleNamespace(
                ntfy=SimpleNamespace(
                    enabled=True,
                    url="https://ntfy.example.test/my-topic",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.ntfy.requests.post",
        fake_post,
    )

    provider = NtfyNotificationServiceProvider()
    ok = provider.send_notification(
        MessageNotification(title="Ntfy Title", message="Ntfy body"),
    )
    assert ok is True
    assert captured["url"] == "https://ntfy.example.test/my-topic"
    assert captured["data"] == b"Ntfy body"
    assert captured["headers"] == {"Title": "MiraMedia - Ntfy Title"}
    assert captured["timeout"] == 60

    monkeypatch.setattr(
        "miramedia.notifications.service_providers.ntfy.requests.post",
        lambda **_kwargs: _fake_response(403),
    )
    assert (
        provider.send_notification(
            MessageNotification(title="x", message="y"),
        )
        is False
    )


def test_pushover_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _fake_response(200)

    monkeypatch.setattr(
        "miramedia.notifications.service_providers.pushover.MiraMediaConfig",
        lambda: SimpleNamespace(
            notifications=SimpleNamespace(
                pushover=SimpleNamespace(
                    enabled=True,
                    api_key="test-token",
                    user="test-user",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.pushover.requests.post",
        fake_post,
    )

    provider = PushoverNotificationServiceProvider()
    ok = provider.send_notification(
        MessageNotification(title="Push Title", message="Push body"),
    )
    assert ok is True
    assert captured["url"] == "https://api.pushover.net/1/messages.json"
    assert captured["params"] == {
        "token": "test-token",
        "user": "test-user",
        "message": "Push body",
        "title": "MiraMedia - Push Title",
    }
    assert captured["timeout"] == 60

    monkeypatch.setattr(
        "miramedia.notifications.service_providers.pushover.requests.post",
        lambda **_kwargs: _fake_response(400),
    )
    assert (
        provider.send_notification(
            MessageNotification(title="x", message="y"),
        )
        is False
    )

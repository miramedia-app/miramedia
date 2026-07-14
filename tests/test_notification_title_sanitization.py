"""Tests for notification title sanitization and provider header safety."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.gotify import (
    GotifyNotificationServiceProvider,
)
from miramedia.notifications.service_providers.ntfy import (
    NtfyNotificationServiceProvider,
)
from miramedia.notifications.utils import sanitize_notification_title


def test_sanitize_notification_title_strips_crlf_and_control_chars() -> None:
    assert sanitize_notification_title("Evil\r\nBcc: x@y") == "EvilBcc: x@y"
    assert sanitize_notification_title("tab\there") == "tabhere"
    assert sanitize_notification_title("del\x7fchar") == "delchar"


def test_sanitize_notification_title_passes_normal_unicode() -> None:
    title = "Download done — S01E02"
    assert sanitize_notification_title(title) == title


def _fake_response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


def test_ntfy_title_header_strips_crlf(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _fake_response(200)

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
    provider.send_notification(
        MessageNotification(title="Evil\r\nInjected", message="body"),
    )

    title_header = captured["headers"]["Title"]
    assert "\r" not in title_header
    assert "\n" not in title_header
    assert title_header == "MiraMedia - EvilInjected"


def test_gotify_sends_token_in_header_not_url(monkeypatch: pytest.MonkeyPatch) -> None:
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
                    api_key="secret-token",
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
    provider.send_notification(
        MessageNotification(title="Title", message="body"),
    )

    assert captured["url"] == "https://gotify.example.test/message"
    assert "token=" not in captured["url"]
    assert captured["headers"] == {"X-Gotify-Key": "secret-token"}

"""Tests for email notification content escaping and subject sanitization."""

from typing import ClassVar

import pytest

from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.email import (
    EmailNotificationServiceProvider,
)


class _FakeEmailConfig:
    emails: ClassVar[list[str]] = ["admin@example.com"]


class _FakeNotifications:
    email_notifications = _FakeEmailConfig()


class _FakeMiraMediaConfig:
    notifications = _FakeNotifications()


@pytest.fixture
def sent_emails(monkeypatch):
    captured: list[dict[str, str]] = []

    def capture_send_email(*, subject: str, html: str, addressee: str) -> None:
        captured.append(
            {"subject": subject, "html": html, "addressee": addressee},
        )

    monkeypatch.setattr(
        "miramedia.notifications.utils.send_email",
        capture_send_email,
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.email.MiraMediaConfig",
        _FakeMiraMediaConfig,
    )
    return captured


def test_email_escapes_html_in_message(sent_emails):
    provider = EmailNotificationServiceProvider()
    provider.send_notification(
        MessageNotification(
            title="Alert",
            message="<script>alert(1)</script>",
        ),
    )

    assert len(sent_emails) == 1
    assert "<script>" not in sent_emails[0]["html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in sent_emails[0]["html"]


def test_email_subject_strips_crlf(sent_emails):
    provider = EmailNotificationServiceProvider()
    provider.send_notification(
        MessageNotification(
            title="Evil\r\nBcc: x@y",
            message="body",
        ),
    )

    assert len(sent_emails) == 1
    subject = sent_emails[0]["subject"]
    assert "\r" not in subject
    assert "\n" not in subject
    assert subject == "MiraMedia - Evil Bcc: x@y"

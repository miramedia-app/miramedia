"""Tests for email notification content escaping and subject sanitization."""

from typing import ClassVar

import pytest

from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.email import (
    EmailNotificationServiceProvider,
)
from miramedia.notifications.utils import send_email


class _FakeEmailConfig:
    emails: ClassVar[list[str]] = ["admin@example.com"]


class _FakeEmailConfigMulti:
    emails: ClassVar[list[str]] = [
        "first@example.com",
        "second@example.com",
        "third@example.com",
    ]


class _FakeSmtpConfig:
    smtp_host = "smtp.example.com"
    smtp_port = 587
    smtp_user = "user"
    smtp_password = "pass"
    from_email = "from@example.com"
    use_tls = False


class _FakeNotifications:
    email_notifications = _FakeEmailConfig()
    smtp_config = _FakeSmtpConfig()


class _FakeNotificationsMulti:
    email_notifications = _FakeEmailConfigMulti()
    smtp_config = _FakeSmtpConfig()


class _FakeMiraMediaConfig:
    notifications = _FakeNotifications()


class _FakeMiraMediaConfigMulti:
    notifications = _FakeNotificationsMulti()


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
    assert provider.send_notification(
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
    assert provider.send_notification(
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


def test_send_email_passes_smtp_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_calls: list[dict[str, object]] = []

    class FakeSMTP:
        def __init__(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
        ) -> None:
            smtp_calls.append({"host": host, "port": port, "timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def starttls(self) -> None:
            pass

        def login(self, user: str, password: str) -> None:
            pass

        def sendmail(self, from_addr: str, to_addrs: str, msg: str) -> None:
            pass

    monkeypatch.setattr(
        "miramedia.notifications.utils.MiraMediaConfig",
        _FakeMiraMediaConfig,
    )
    monkeypatch.setattr("miramedia.notifications.utils.smtplib.SMTP", FakeSMTP)

    send_email(subject="Test", html="<p>hi</p>", addressee="to@example.com")

    assert len(smtp_calls) == 1
    assert smtp_calls[0]["timeout"] == 60


def test_email_continues_after_recipient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def send_email_fail_first(
        *,
        subject: str,
        html: str,
        addressee: str,
    ) -> None:
        del subject, html
        calls.append(addressee)
        if addressee == "first@example.com":
            err = "connection refused"
            raise OSError(err)

    monkeypatch.setattr(
        "miramedia.notifications.utils.send_email",
        send_email_fail_first,
    )
    monkeypatch.setattr(
        "miramedia.notifications.service_providers.email.MiraMediaConfig",
        _FakeMiraMediaConfigMulti,
    )

    provider = EmailNotificationServiceProvider()
    result = provider.send_notification(
        MessageNotification(title="Alert", message="body"),
    )

    assert calls == [
        "first@example.com",
        "second@example.com",
        "third@example.com",
    ]
    assert result is False

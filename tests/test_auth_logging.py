"""Tests that auth logging never leaks verification or reset tokens."""

from __future__ import annotations

import asyncio
import logging
import types
import uuid
from collections.abc import Mapping, Sequence


def _make_user_manager():
    from miramedia.auth.users import UserManager

    return UserManager.__new__(UserManager)


def _make_stub_user():
    return types.SimpleNamespace(id=uuid.uuid4(), email="stub@example.com")


def _assert_no_secret_in_value(
    value: object,
    *,
    token: str,
    path: str,
    seen: set[int],
) -> None:
    if isinstance(value, (str, bytes, bytearray)):
        text = (
            value.decode("utf-8", errors="replace")
            if isinstance(value, (bytes, bytearray))
            else value
        )
        assert token not in text, f"Token found in log field {path}: {text!r}"
        token_url = f"token={token}"
        assert token_url not in text, (
            f"Token-bearing URL found in log field {path}: {text!r}"
        )
        return

    obj_id = id(value)
    if obj_id in seen:
        return
    seen.add(obj_id)

    if isinstance(value, Mapping):
        for key, nested in value.items():
            _assert_no_secret_in_value(
                key, token=token, path=f"{path}.key[{key!r}]", seen=seen
            )
            _assert_no_secret_in_value(
                nested, token=token, path=f"{path}[{key!r}]", seen=seen
            )
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _assert_no_secret_in_value(
                nested, token=token, path=f"{path}[{index}]", seen=seen
            )
        return

    representation = repr(value)
    assert token not in representation, (
        f"Token found in repr at {path}: {representation!r}"
    )


def _assert_no_secret_in_log_record(record: logging.LogRecord, token: str) -> None:
    seen: set[int] = set()
    _assert_no_secret_in_value(record.msg, token=token, path="msg", seen=seen)
    _assert_no_secret_in_value(record.args, token=token, path="args", seen=seen)
    _assert_no_secret_in_value(record.__dict__, token=token, path="record", seen=seen)
    _assert_no_secret_in_value(record, token=token, path="repr(record)", seen=seen)


def test_token_not_logged_when_email_enabled(monkeypatch, caplog):
    """When email resets are enabled, the reset token must not appear in any log record."""
    from miramedia.config import MiraMediaConfig

    monkeypatch.setattr(MiraMediaConfig().auth, "email_password_resets", True)
    monkeypatch.setattr(
        "miramedia.notifications.utils.send_email",
        lambda **_kwargs: None,
    )

    manager = _make_user_manager()
    user = _make_stub_user()
    token = "supersecrettoken123"

    with caplog.at_level(logging.DEBUG, logger="miramedia.auth.users"):
        asyncio.run(manager.on_after_forgot_password(user, token))

    for record in caplog.records:
        _assert_no_secret_in_log_record(record, token)


def test_verification_token_not_logged(caplog):
    """Verification tokens must not appear anywhere in captured log records."""
    manager = _make_user_manager()
    user = _make_stub_user()
    token = "unique-verification-sentinel-abc123"

    with caplog.at_level(logging.DEBUG, logger="miramedia.auth.users"):
        asyncio.run(manager.on_after_request_verify(user, token))

    for record in caplog.records:
        _assert_no_secret_in_log_record(record, token)

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert info_records, "Expected an INFO log for verification request"
    assert str(user.id) in info_records[0].getMessage()


def test_link_logged_as_warning_when_email_disabled(monkeypatch, caplog):
    """When email resets are disabled, exactly one WARNING record containing the link is emitted."""
    from miramedia.config import MiraMediaConfig

    monkeypatch.setattr(MiraMediaConfig().auth, "email_password_resets", False)

    manager = _make_user_manager()
    user = _make_stub_user()
    token = "supersecrettoken456"

    with caplog.at_level(logging.DEBUG, logger="miramedia.auth.users"):
        asyncio.run(manager.on_after_forgot_password(user, token))

    frontend_url = MiraMediaConfig().misc.frontend_url
    expected_link = f"{frontend_url}web/login/reset-password?token={token}"

    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and expected_link in r.getMessage()
    ]
    assert len(warning_records) == 1, (
        f"Expected exactly 1 WARNING record containing the link, got {len(warning_records)}. "
        f"Records: {[r.getMessage() for r in caplog.records]}"
    )

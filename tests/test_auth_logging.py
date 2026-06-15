"""Tests that password-reset logging never leaks the token when email is enabled."""

import asyncio
import logging
import types
import uuid


def _make_user_manager():
    """Return a UserManager instance without calling __init__.

    NOTES: UserManager.__init__ (inherited from BaseUserManager) requires a
    user_db argument that would pull in SQLAlchemy / DB setup.  The method
    under test (on_after_forgot_password) only calls log.*, config lookups,
    and send_email — none of which touch self.user_db — so bypassing __init__
    is safe and documented by the plan.
    """
    from miramedia.auth.users import UserManager

    return UserManager.__new__(UserManager)


def _make_stub_user():
    """Return a minimal stub object that satisfies user.id and user.email."""
    return types.SimpleNamespace(id=uuid.uuid4(), email="stub@example.com")


def test_token_not_logged_when_email_enabled(monkeypatch, caplog):
    """When email resets are enabled, the reset token must not appear in any log record."""
    import miramedia.auth.users as auth_users

    # Enable email password resets.
    monkeypatch.setattr(auth_users.config, "email_password_resets", True)

    # Stub out send_email so no SMTP is attempted.
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
        assert token not in record.getMessage(), (
            f"Reset token found in log record at level {record.levelname}: {record.getMessage()!r}"
        )


def test_link_logged_as_warning_when_email_disabled(monkeypatch, caplog):
    """When email resets are disabled, exactly one WARNING record containing the link is emitted."""
    import miramedia.auth.users as auth_users
    from miramedia.config import MiraMediaConfig

    # Disable email password resets.
    monkeypatch.setattr(auth_users.config, "email_password_resets", False)

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

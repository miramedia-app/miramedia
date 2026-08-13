"""Unit tests for UserManager password policy enforcement."""

from __future__ import annotations

import asyncio
import types
import uuid

import pytest
from fastapi_users.exceptions import InvalidPasswordException

from miramedia.auth.schemas import UserCreate
from miramedia.auth.users import UserManager


def _make_user_manager() -> UserManager:
    return UserManager.__new__(UserManager)


def _make_stub_user(email: str = "user@example.com") -> types.SimpleNamespace:
    return types.SimpleNamespace(id=uuid.uuid4(), email=email)


@pytest.mark.parametrize(
    "password",
    [
        "",
        "short",
        "1234567",
    ],
)
def test_validate_password_rejects_short_passwords(password: str) -> None:
    manager = _make_user_manager()
    user = _make_stub_user()

    with pytest.raises(InvalidPasswordException) as exc_info:
        asyncio.run(manager.validate_password(password, user))
    assert exc_info.value.reason == "Password must be at least 8 characters"


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("user@example.com", "user@example.com"),
        ("User@Example.com", "user@example.com"),
        ("user@example.com", "USER@EXAMPLE.COM"),
    ],
)
def test_validate_password_rejects_email_as_password(email: str, password: str) -> None:
    manager = _make_user_manager()
    user = _make_stub_user(email=email)

    with pytest.raises(InvalidPasswordException) as exc_info:
        asyncio.run(manager.validate_password(password, user))
    assert exc_info.value.reason == (
        "Password must not be the same as the email address"
    )


def test_validate_password_accepts_valid_password() -> None:
    manager = _make_user_manager()
    user = _make_stub_user()

    asyncio.run(manager.validate_password("long-enough-password", user))


def test_validate_password_accepts_valid_user_create() -> None:
    manager = _make_user_manager()
    user_create = UserCreate(
        email="newuser@example.com",
        password="long-enough-password",
    )

    asyncio.run(manager.validate_password("long-enough-password", user_create))

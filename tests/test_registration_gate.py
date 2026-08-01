"""Tests for the auth.allow_registration gate on /auth/register."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from miramedia.config import MiraMediaConfig


def test_registration_disabled_by_default() -> None:
    from miramedia.main import _registration_enabled

    assert MiraMediaConfig().auth.allow_registration is False
    with pytest.raises(HTTPException) as exc_info:
        _registration_enabled()
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Registration is disabled"


def test_registration_enabled_when_config_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.main import _registration_enabled

    monkeypatch.setattr(MiraMediaConfig().auth, "allow_registration", True)
    _registration_enabled()

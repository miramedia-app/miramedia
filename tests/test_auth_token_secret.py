import logging

import pytest
from pydantic import ValidationError

from miramedia.auth.config import PLACEHOLDER_TOKEN_SECRET, AuthConfig


def test_placeholder_token_secret_raises() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(token_secret=PLACEHOLDER_TOKEN_SECRET)


def test_short_token_secret_raises() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(token_secret="too-short")


def test_valid_hex_token_secret_accepted() -> None:
    secret = "a" * 64
    cfg = AuthConfig(token_secret=secret)
    assert cfg.token_secret == secret


def test_allow_insecure_replaces_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRAMEDIA_ALLOW_INSECURE_TOKEN_SECRET", "1")
    cfg = AuthConfig(token_secret=PLACEHOLDER_TOKEN_SECRET)
    assert cfg.token_secret != PLACEHOLDER_TOKEN_SECRET
    assert len(cfg.token_secret) >= 32


def test_unset_token_secret_warns_and_generates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="miramedia.auth.config"):
        cfg = AuthConfig()
    assert len(cfg.token_secret) == 64
    assert all(c in "0123456789abcdef" for c in cfg.token_secret)
    assert any("ephemeral" in r.message for r in caplog.records)


def test_configured_token_secret_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "b" * 64
    with caplog.at_level(logging.WARNING, logger="miramedia.auth.config"):
        cfg = AuthConfig(token_secret=secret)
    assert cfg.token_secret == secret
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)

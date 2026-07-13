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

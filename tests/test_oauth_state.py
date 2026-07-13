"""OAuth authorize snapshot encryption tests."""

from __future__ import annotations

import time

import pytest

from miramedia.auth.oauth_state import (
    OAuthAuthorizeSnapshot,
    OAuthAuthorizeSnapshotError,
    decrypt_oauth_authorize_snapshot,
    encrypt_oauth_authorize_snapshot,
)
from miramedia.auth.runtime import OAUTH_ROUTE_NAME


def test_encrypt_decrypt_round_trip_preserves_opaque_credential_whitespace() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id=" client-id ",
        client_secret=" secret-with-spaces ",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "a" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    restored = decrypt_oauth_authorize_snapshot(token, secret)
    assert restored.client_id == " client-id "
    assert restored.client_secret == " secret-with-spaces "
    assert restored == snapshot


def test_decrypt_does_not_generate_ephemeral_token_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "a" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    with caplog.at_level("WARNING"):
        decrypt_oauth_authorize_snapshot(token, secret)
    assert "ephemeral random secret" not in caplog.text


@pytest.mark.parametrize(
    ("session_lifetime", "accepted"),
    [
        (1, True),
        (60 * 60 * 24 * 366, True),
        (0, False),
        (-1, False),
        (60 * 60 * 24 * 366 + 1, False),
    ],
)
def test_snapshot_session_lifetime_boundaries(
    session_lifetime: int,
    accepted: bool,
) -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=session_lifetime,
    )
    secret = "a" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    if accepted:
        restored = decrypt_oauth_authorize_snapshot(token, secret)
        assert restored.session_lifetime == session_lifetime
    else:
        with pytest.raises(OAuthAuthorizeSnapshotError):
            decrypt_oauth_authorize_snapshot(token, secret)


def test_encrypt_decrypt_round_trip() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "a" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    restored = decrypt_oauth_authorize_snapshot(token, secret)
    assert restored == snapshot


def test_decrypt_rejects_tampered_snapshot() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "b" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    with pytest.raises(OAuthAuthorizeSnapshotError):
        decrypt_oauth_authorize_snapshot(
            token[:-1] + ("x" if token[-1] != "x" else "y"), secret
        )


def test_decrypt_rejects_wrong_secret() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    token = encrypt_oauth_authorize_snapshot(snapshot, "c" * 64)
    with pytest.raises(OAuthAuthorizeSnapshotError):
        decrypt_oauth_authorize_snapshot(token, "d" * 64)


def test_decrypt_rejects_string_bool_cookie_secure() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost:8000/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "f" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    raw = (
        __import__("miramedia.auth.oauth_state", fromlist=["_fernet_for_state_secret"])
        ._fernet_for_state_secret(secret)
        .decrypt(token.encode("ascii"))
    )
    data = __import__("json").loads(raw.decode("utf-8"))
    data["cookie_secure"] = "false"
    tampered = (
        __import__("miramedia.auth.oauth_state", fromlist=["_fernet_for_state_secret"])
        ._fernet_for_state_secret(secret)
        .encrypt(__import__("json").dumps(data).encode("utf-8"))
        .decode("ascii")
    )
    with pytest.raises(OAuthAuthorizeSnapshotError):
        decrypt_oauth_authorize_snapshot(tampered, secret)


def test_decrypt_rejects_expired_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        provider_name="Display",
        account_provider_name=OAUTH_ROUTE_NAME,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "e" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    issued_at = time.time()
    monkeypatch.setattr(time, "time", lambda: issued_at + 7200)
    with pytest.raises(OAuthAuthorizeSnapshotError):
        decrypt_oauth_authorize_snapshot(token, secret)

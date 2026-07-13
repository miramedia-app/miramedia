"""Shared OAuth issuer fixtures for tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from miramedia.auth.oauth_identity import provider_key_from_issuer

ISSUER_A = "https://issuer-a.example/"
ISSUER_B = "https://issuer-b.example/"
ENDPOINT_A = "https://issuer-a.example/.well-known/openid-configuration"
ENDPOINT_B = "https://issuer-b.example/.well-known/openid-configuration"
ENDPOINT_DEFAULT = "https://idp.example/.well-known/openid-configuration"
ISSUER_DEFAULT = "https://idp.example/"
KEY_A = provider_key_from_issuer(ISSUER_A)
KEY_B = provider_key_from_issuer(ISSUER_B)
KEY_DEFAULT = provider_key_from_issuer(ISSUER_DEFAULT)

ENDPOINT_ISSUERS: dict[str, str] = {
    ENDPOINT_A: ISSUER_A,
    ENDPOINT_B: ISSUER_B,
    ENDPOINT_DEFAULT: ISSUER_DEFAULT,
}


def discovery_configuration_for_endpoint(
    endpoint: str, *, issuer: str | None = None
) -> dict[str, Any]:
    resolved_issuer = (
        issuer if issuer is not None else ENDPOINT_ISSUERS.get(endpoint, ISSUER_DEFAULT)
    )
    base = resolved_issuer.rstrip("/")
    return {
        "issuer": resolved_issuer,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "userinfo_endpoint": f"{base}/userinfo",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    }


def build_openid_client_mock(
    *,
    endpoint: str,
    issuer: str | None = None,
    name: str = "Provider",
    client_id: str = "client-a",
    client_secret: str = "secret",  # noqa: S107
) -> MagicMock:
    client = MagicMock()
    client.client_id = client_id
    client.client_secret = client_secret
    client.name = name
    client.openid_configuration = discovery_configuration_for_endpoint(
        endpoint,
        issuer=issuer,
    )

    async def _authorize_url(
        redirect_url: str, state: str, *_args: object, **_kwargs: object
    ) -> str:
        return (
            f"https://idp.example/authorize?state={state}&redirect_uri={redirect_url}"
        )

    client.get_authorization_url = AsyncMock(side_effect=_authorize_url)
    client.get_access_token = AsyncMock(
        return_value={"access_token": "access-token", "token_type": "bearer"}
    )
    client.get_id_email = AsyncMock(return_value=("account-1", "user@example.com"))
    return client


def install_openid_client_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[MagicMock] = []

    def _factory(**kwargs: object) -> MagicMock:
        endpoint = str(kwargs.get("openid_configuration_endpoint", ENDPOINT_DEFAULT))
        client = build_openid_client_mock(
            endpoint=endpoint,
            name=str(kwargs.get("name", "Provider")),
            client_id=str(kwargs.get("client_id", "client-a")),
            client_secret=str(kwargs.get("client_secret", "secret")),
        )
        created.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)

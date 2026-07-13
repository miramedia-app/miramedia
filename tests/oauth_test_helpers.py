"""Shared OAuth issuer fixtures for tests."""

from __future__ import annotations

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


def install_issuer_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = {
        ENDPOINT_A: ISSUER_A,
        ENDPOINT_B: ISSUER_B,
        ENDPOINT_DEFAULT: ISSUER_DEFAULT,
    }

    async def _resolve(configuration_endpoint: str) -> str:
        issuer = mapping.get(configuration_endpoint)
        if issuer is None:
            from miramedia.auth.oauth_identity import OpenIdIssuerResolutionError

            msg = "unknown configuration endpoint"
            raise OpenIdIssuerResolutionError(msg)
        return provider_key_from_issuer(issuer)

    monkeypatch.setattr(
        "miramedia.auth.runtime.resolve_openid_provider_key",
        _resolve,
    )

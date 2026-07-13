"""Issuer-derived OAuth provider identity for persisted account rows."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

ISSUER_DERIVED_PREFIX = "oidc:"
_ISSUER_FETCH_TIMEOUT_SECONDS = 15.0


class OpenIdIssuerResolutionError(Exception):
    """OIDC discovery metadata could not be resolved to a valid issuer."""


def is_issuer_derived_provider_key(oauth_name: str) -> bool:
    return oauth_name.startswith(ISSUER_DERIVED_PREFIX)


def provider_key_from_issuer(issuer: str) -> str:
    """Derive a bounded persisted provider key from the exact discovery issuer."""
    return (
        f"{ISSUER_DERIVED_PREFIX}{hashlib.sha256(issuer.encode('utf-8')).hexdigest()}"
    )


def validate_discovery_issuer(value: Any) -> str:  # noqa: ANN401
    if not isinstance(value, str):
        msg = "OIDC discovery issuer must be a string"
        raise OpenIdIssuerResolutionError(msg)
    issuer = value.strip()
    if not issuer:
        msg = "OIDC discovery metadata missing issuer"
        raise OpenIdIssuerResolutionError(msg)
    parsed = urlparse(issuer)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        msg = "OIDC discovery issuer is invalid"
        raise OpenIdIssuerResolutionError(msg)
    return issuer


def _fetch_openid_discovery_issuer_sync(configuration_endpoint: str) -> str:
    try:
        response = httpx.get(
            configuration_endpoint,
            timeout=_ISSUER_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log.warning(
            "OpenID Connect discovery failed: %s",
            type(exc).__name__,
        )
        msg = "OIDC discovery failed"
        raise OpenIdIssuerResolutionError(msg) from exc
    if not isinstance(payload, dict):
        msg = "OIDC discovery metadata is malformed"
        raise OpenIdIssuerResolutionError(msg)
    return validate_discovery_issuer(payload.get("issuer"))


async def resolve_openid_provider_key(configuration_endpoint: str) -> str:
    """Resolve the persisted provider key for one OIDC configuration endpoint."""
    import asyncio

    issuer = await asyncio.to_thread(
        _fetch_openid_discovery_issuer_sync,
        configuration_endpoint,
    )
    return provider_key_from_issuer(issuer)


def validate_provider_key_for_snapshot(oauth_name: str) -> str:
    if not is_issuer_derived_provider_key(oauth_name):
        msg = "snapshot provider key is invalid"
        raise ValueError(msg)
    suffix = oauth_name[len(ISSUER_DERIVED_PREFIX) :]
    if len(suffix) != 64 or any(ch not in "0123456789abcdef" for ch in suffix):
        msg = "snapshot provider key is invalid"
        raise ValueError(msg)
    return oauth_name

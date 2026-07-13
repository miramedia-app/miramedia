"""Issuer-derived OAuth provider identity for persisted account rows."""

from __future__ import annotations

import hashlib
import ipaddress
from typing import Any
from urllib.parse import urlparse

ISSUER_DERIVED_PREFIX = "oidc:"


class OpenIdIssuerResolutionError(Exception):
    """OIDC discovery metadata could not be resolved to a valid issuer."""


def is_issuer_derived_provider_key(oauth_name: str) -> bool:
    return oauth_name.startswith(ISSUER_DERIVED_PREFIX)


def provider_key_from_issuer(issuer: str) -> str:
    """Derive a bounded persisted provider key from the exact discovery issuer."""
    return (
        f"{ISSUER_DERIVED_PREFIX}{hashlib.sha256(issuer.encode('utf-8')).hexdigest()}"
    )


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_discovery_issuer(value: Any) -> str:  # noqa: ANN401
    if not isinstance(value, str):
        msg = "OIDC discovery issuer must be a string"
        raise OpenIdIssuerResolutionError(msg)
    if not value:
        msg = "OIDC discovery metadata missing issuer"
        raise OpenIdIssuerResolutionError(msg)
    if value != value.strip():
        msg = "OIDC discovery issuer is invalid"
        raise OpenIdIssuerResolutionError(msg)
    parsed = urlparse(value)
    if parsed.username or parsed.password:
        msg = "OIDC discovery issuer is invalid"
        raise OpenIdIssuerResolutionError(msg)
    if parsed.query or parsed.fragment:
        msg = "OIDC discovery issuer is invalid"
        raise OpenIdIssuerResolutionError(msg)
    if not parsed.scheme or not parsed.netloc:
        msg = "OIDC discovery issuer is invalid"
        raise OpenIdIssuerResolutionError(msg)
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http" and _is_loopback_host(parsed.hostname):
        return value
    msg = "OIDC discovery issuer is invalid"
    raise OpenIdIssuerResolutionError(msg)


def provider_identity_from_openid_configuration(
    openid_configuration: dict[str, Any],
) -> tuple[str, str]:
    """Return the exact issuer and derived provider key from one discovery document."""
    if not isinstance(openid_configuration, dict):
        msg = "OIDC discovery metadata is malformed"
        raise OpenIdIssuerResolutionError(msg)
    issuer = validate_discovery_issuer(openid_configuration.get("issuer"))
    return issuer, provider_key_from_issuer(issuer)


def assert_snapshot_provider_identity(
    *,
    snapshot_issuer: str,
    snapshot_provider_key: str,
    openid_configuration: dict[str, Any],
) -> str:
    """Validate callback-time discovery matches the authorize snapshot."""
    client_issuer, client_key = provider_identity_from_openid_configuration(
        openid_configuration
    )
    if client_issuer != snapshot_issuer:
        msg = "snapshot issuer mismatch"
        raise ValueError(msg)
    if client_key != snapshot_provider_key:
        msg = "snapshot provider key mismatch"
        raise ValueError(msg)
    return client_issuer


def validate_provider_key_for_snapshot(oauth_name: str) -> str:
    if not is_issuer_derived_provider_key(oauth_name):
        msg = "snapshot provider key is invalid"
        raise ValueError(msg)
    suffix = oauth_name[len(ISSUER_DERIVED_PREFIX) :]
    if len(suffix) != 64 or any(ch not in "0123456789abcdef" for ch in suffix):
        msg = "snapshot provider key is invalid"
        raise ValueError(msg)
    return oauth_name

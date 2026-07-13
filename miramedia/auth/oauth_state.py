"""Cross-worker OAuth authorize snapshot encryption for state round-trips."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi_users.jwt import SecretType

from miramedia.auth.config import OpenIdConfig
from miramedia.auth.runtime import (
    OAUTH_ROUTE_NAME,
    AuthRuntimeGeneration,
    _build_openid_client_sync,
)

log = logging.getLogger(__name__)

OAUTH_GENERATION_STATE_KEY = "generation_snapshot"
OAUTH_STATE_GENERATION_TTL_SECONDS = 3600
_OAUTH_SNAPSHOT_KDF_INFO = b"miramedia-oauth-generation-snapshot-v1"


_OIDC_NOT_ENABLED = "OIDC is not enabled"
_SNAPSHOT_INVALID = "snapshot invalid or expired"
_SNAPSHOT_MALFORMED = "snapshot payload malformed"
_SNAPSHOT_DISABLED = "OIDC disabled in snapshot"
_SNAPSHOT_CLIENT_INVALID = "snapshot client invalid"


class OAuthAuthorizeSnapshotError(Exception):
    """Encrypted authorize snapshot is missing, tampered, or expired."""


@dataclass(frozen=True, slots=True)
class OAuthAuthorizeSnapshot:
    """Minimal authorize-time OIDC runtime for callback reconstruction."""

    client_id: str
    client_secret: str
    configuration_endpoint: str
    provider_name: str
    account_provider_name: str
    frontend_url: str
    cookie_secure: bool
    session_lifetime: int
    oidc_enabled: bool = True


def _secret_bytes(secret: SecretType) -> bytes:
    if isinstance(secret, str):
        return secret.encode("utf-8")
    return secret.get_secret_value().encode("utf-8")


def _fernet_for_state_secret(secret: SecretType) -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_OAUTH_SNAPSHOT_KDF_INFO,
    ).derive(_secret_bytes(secret))
    return Fernet(base64.urlsafe_b64encode(derived))


def snapshot_from_generation(
    generation: AuthRuntimeGeneration,
) -> OAuthAuthorizeSnapshot:
    if not generation.oidc_enabled or generation.client is None:
        msg = _OIDC_NOT_ENABLED
        raise OAuthAuthorizeSnapshotError(msg)
    client = generation.client
    return OAuthAuthorizeSnapshot(
        client_id=str(client.client_id),
        client_secret=str(client.client_secret),
        configuration_endpoint=str(generation.configuration_endpoint),
        provider_name=generation.provider_name,
        account_provider_name=generation.account_provider_name,
        frontend_url=generation.frontend_url,
        cookie_secure=generation.cookie_secure,
        session_lifetime=generation.session_lifetime,
        oidc_enabled=True,
    )


def encrypt_oauth_authorize_snapshot(
    snapshot: OAuthAuthorizeSnapshot,
    state_secret: SecretType,
) -> str:
    payload = json.dumps(asdict(snapshot), separators=(",", ":")).encode("utf-8")
    return _fernet_for_state_secret(state_secret).encrypt(payload).decode("ascii")


def decrypt_oauth_authorize_snapshot(
    token: str,
    state_secret: SecretType,
) -> OAuthAuthorizeSnapshot:
    try:
        raw = _fernet_for_state_secret(state_secret).decrypt(
            token.encode("ascii"),
            ttl=OAUTH_STATE_GENERATION_TTL_SECONDS,
        )
    except InvalidToken as exc:
        msg = _SNAPSHOT_INVALID
        raise OAuthAuthorizeSnapshotError(msg) from exc
    try:
        data: dict[str, Any] = json.loads(raw.decode("utf-8"))
        snapshot = OAuthAuthorizeSnapshot(
            client_id=str(data["client_id"]),
            client_secret=str(data["client_secret"]),
            configuration_endpoint=str(data["configuration_endpoint"]),
            provider_name=str(data["provider_name"]),
            account_provider_name=str(data["account_provider_name"]),
            frontend_url=str(data["frontend_url"]),
            cookie_secure=bool(data["cookie_secure"]),
            session_lifetime=int(data["session_lifetime"]),
            oidc_enabled=bool(data.get("oidc_enabled", True)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        msg = _SNAPSHOT_MALFORMED
        raise OAuthAuthorizeSnapshotError(msg) from exc
    if not snapshot.oidc_enabled:
        msg = _SNAPSHOT_DISABLED
        raise OAuthAuthorizeSnapshotError(msg)
    return snapshot


async def auth_runtime_generation_from_snapshot(
    snapshot: OAuthAuthorizeSnapshot,
) -> AuthRuntimeGeneration:
    oidc = OpenIdConfig(
        enabled=True,
        name=snapshot.provider_name,
        client_id=snapshot.client_id,
        client_secret=snapshot.client_secret,
        configuration_endpoint=snapshot.configuration_endpoint,
    )
    try:
        client = await asyncio.to_thread(_build_openid_client_sync, oidc)
    except Exception as exc:
        log.warning(
            "OAuth authorize snapshot client activation failed: %s",
            type(exc).__name__,
        )
        msg = _SNAPSHOT_CLIENT_INVALID
        raise OAuthAuthorizeSnapshotError(msg) from exc
    return AuthRuntimeGeneration(
        generation_id=0,
        oidc_enabled=True,
        provider_name=snapshot.provider_name,
        account_provider_name=snapshot.account_provider_name or OAUTH_ROUTE_NAME,
        client=client,
        cookie_secure=snapshot.cookie_secure,
        frontend_url=snapshot.frontend_url,
        session_lifetime=snapshot.session_lifetime,
    )

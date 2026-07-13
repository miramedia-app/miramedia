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
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from miramedia.auth.config import OpenIdConfig, validate_session_lifetime_value
from miramedia.auth.runtime import (
    OAUTH_ROUTE_NAME,
    AuthRuntimeGeneration,
    _build_openid_client_sync,
)
from miramedia.config import BasicConfig

log = logging.getLogger(__name__)

OAUTH_GENERATION_STATE_KEY = "generation_snapshot"
OAUTH_STATE_GENERATION_TTL_SECONDS = 3600
_OAUTH_SNAPSHOT_KDF_INFO = b"miramedia-oauth-generation-snapshot-v1"
_HTTP_URL_ADAPTER: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)

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


def _strict_bool(value: Any, field: str) -> bool:  # noqa: ANN401
    if type(value) is bool:
        return value
    msg = f"{field} must be a boolean"
    raise ValueError(msg)


def validate_snapshot_session_lifetime(value: Any) -> int:  # noqa: ANN401
    """Validate snapshot session lifetime using the canonical auth contract."""
    if type(value) is not int or isinstance(value, bool):
        msg = "session_lifetime must be an integer"
        raise ValueError(msg)
    try:
        return validate_session_lifetime_value(value)
    except ValueError as exc:
        msg = "session_lifetime out of allowed range"
        raise ValueError(msg) from exc


def _nonempty_str(value: Any, field: str) -> str:  # noqa: ANN401
    if not isinstance(value, str):
        msg = f"{field} must be a string"
        raise TypeError(msg)
    stripped = value.strip()
    if not stripped:
        msg = f"{field} must be non-empty"
        raise ValueError(msg)
    return stripped


def _opaque_credential_str(value: Any, field: str) -> str:  # noqa: ANN401
    if not isinstance(value, str):
        msg = f"{field} must be a string"
        raise TypeError(msg)
    if not value.strip():
        msg = f"{field} must be non-empty"
        raise ValueError(msg)
    return value


def _validate_snapshot_payload(data: dict[str, Any]) -> OAuthAuthorizeSnapshot:
    try:
        cookie_secure = _strict_bool(data["cookie_secure"], "cookie_secure")
        oidc_enabled = _strict_bool(data.get("oidc_enabled", True), "oidc_enabled")
        session_lifetime = validate_snapshot_session_lifetime(data["session_lifetime"])
        client_id = _opaque_credential_str(data["client_id"], "client_id")
        client_secret = _opaque_credential_str(data["client_secret"], "client_secret")
        configuration_endpoint = _nonempty_str(
            data["configuration_endpoint"], "configuration_endpoint"
        )
        provider_name = _nonempty_str(data["provider_name"], "provider_name")
        account_provider_name = _nonempty_str(
            data["account_provider_name"], "account_provider_name"
        )
        frontend_url = _nonempty_str(data["frontend_url"], "frontend_url")
        frontend_http_url = _HTTP_URL_ADAPTER.validate_python(frontend_url)
        OpenIdConfig(
            enabled=True,
            name=provider_name,
            client_id=client_id,
            client_secret=client_secret,
            configuration_endpoint=configuration_endpoint,
        )
        BasicConfig(frontend_url=frontend_http_url)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        msg = _SNAPSHOT_MALFORMED
        raise OAuthAuthorizeSnapshotError(msg) from exc
    if not oidc_enabled:
        msg = _SNAPSHOT_DISABLED
        raise OAuthAuthorizeSnapshotError(msg)
    return OAuthAuthorizeSnapshot(
        client_id=client_id,
        client_secret=client_secret,
        configuration_endpoint=configuration_endpoint,
        provider_name=provider_name,
        account_provider_name=account_provider_name,
        frontend_url=str(frontend_http_url),
        cookie_secure=cookie_secure,
        session_lifetime=session_lifetime,
        oidc_enabled=True,
    )


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
    except (json.JSONDecodeError, ValueError) as exc:
        msg = _SNAPSHOT_MALFORMED
        raise OAuthAuthorizeSnapshotError(msg) from exc
    if not isinstance(data, dict):
        msg = _SNAPSHOT_MALFORMED
        raise OAuthAuthorizeSnapshotError(msg)
    return _validate_snapshot_payload(data)


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
        configuration_endpoint=snapshot.configuration_endpoint,
        cookie_secure=snapshot.cookie_secure,
        frontend_url=snapshot.frontend_url,
        session_lifetime=snapshot.session_lifetime,
    )

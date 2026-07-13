"""Auth runtime lifecycle: staged OIDC activation, atomic swaps, request snapshots."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Literal

from fastapi import HTTPException, Request, status
from httpx_oauth.clients.openid import OpenID
from httpx_oauth.oauth2 import BaseOAuth2, OAuth2Token
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from miramedia.auth.config import AuthConfig, OpenIdConfig
from miramedia.auth.oauth_identity import (
    OpenIdIssuerResolutionError,
    provider_identity_from_openid_configuration,
)
from miramedia.config import BasicConfig, MiraMediaConfig
from miramedia.settings.service import build_isolated_config

log = logging.getLogger(__name__)

OAUTH_ROUTE_NAME = "oidc"
OIDC_CONFIG_INVALID_DETAIL = "OpenID Connect provider configuration is invalid."
_oauth_runtime_ctx: ContextVar[AuthRuntimeGeneration | None] = ContextVar(
    "auth_oidc_runtime_generation", default=None
)


class AuthRuntimeActivationError(Exception):
    """Prospective auth runtime could not be built or activated."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or OIDC_CONFIG_INVALID_DETAIL)


@dataclass(frozen=True, slots=True)
class AuthRuntimeGeneration:
    """Immutable OIDC runtime generation swapped atomically."""

    generation_id: int
    oidc_enabled: bool
    provider_name: str
    account_provider_name: str
    openid_issuer: str = ""
    client: OpenID | None = None
    configuration_endpoint: str = ""
    cookie_secure: bool = False
    frontend_url: str = ""
    session_lifetime: int = 3600


def join_frontend_path(frontend_url: str, path: str) -> str:
    """Join frontend base URL and relative path without missing/double slashes."""
    return f"{frontend_url.rstrip('/')}/{path.lstrip('/')}"


class AuthRuntimeStore:
    """Thread-safe holder for the active OIDC runtime generation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._active = AuthRuntimeGeneration(
            generation_id=0,
            oidc_enabled=False,
            provider_name="",
            account_provider_name="",
            openid_issuer="",
            client=None,
            configuration_endpoint="",
            cookie_secure=False,
            frontend_url="",
            session_lifetime=3600,
        )

    def get_active(self) -> AuthRuntimeGeneration:
        return self._active

    def swap(self, prospective: AuthRuntimeGeneration) -> AuthRuntimeGeneration:
        with self._lock:
            activated = replace(prospective, generation_id=self._next_id)
            self._next_id += 1
            self._active = activated
            return activated

    def restore(self, generation: AuthRuntimeGeneration) -> AuthRuntimeGeneration:
        with self._lock:
            self._active = generation
            return self._active

    def reset_for_tests(self) -> AuthRuntimeGeneration:
        with self._lock:
            self._next_id = 1
            self._active = AuthRuntimeGeneration(
                generation_id=0,
                oidc_enabled=False,
                provider_name="",
                account_provider_name="",
                openid_issuer="",
                client=None,
                cookie_secure=False,
                frontend_url="",
                session_lifetime=3600,
            )
            return self._active


auth_runtime_store = AuthRuntimeStore()


def get_live_auth_config() -> AuthConfig:
    return MiraMediaConfig().auth


def preview_auth_config(overrides: dict) -> AuthConfig:
    """Build prospective auth settings without mutating the live singleton."""
    return build_isolated_config(overrides).auth


def _build_openid_client_sync(oidc: OpenIdConfig) -> OpenID:
    log.info("Configured OIDC provider: %s", oidc.name)
    return OpenID(
        base_scopes=["openid", "email", "profile"],
        client_id=oidc.client_id,
        client_secret=oidc.client_secret,
        name=oidc.name,
        openid_configuration_endpoint=oidc.configuration_endpoint,
    )


def _cookie_secure_from_config(auth: AuthConfig, misc: BasicConfig) -> bool:
    if auth.cookie_secure is not None:
        return auth.cookie_secure
    return str(misc.frontend_url).startswith("https://")


async def build_auth_runtime_generation(
    auth_config: AuthConfig,
    misc_config: BasicConfig,
) -> AuthRuntimeGeneration:
    """Validate/build a prospective OIDC runtime off the async event loop."""
    cookie_secure = _cookie_secure_from_config(auth_config, misc_config)
    frontend_url = str(misc_config.frontend_url)
    session_lifetime = auth_config.session_lifetime
    oidc = auth_config.openid_connect
    if not oidc.enabled:
        return AuthRuntimeGeneration(
            generation_id=0,
            oidc_enabled=False,
            provider_name="",
            account_provider_name="",
            openid_issuer="",
            client=None,
            cookie_secure=cookie_secure,
            frontend_url=frontend_url,
            session_lifetime=session_lifetime,
        )
    try:
        client = await asyncio.to_thread(_build_openid_client_sync, oidc)
        openid_issuer, provider_key = provider_identity_from_openid_configuration(
            client.openid_configuration
        )
    except OpenIdIssuerResolutionError as exc:
        raise AuthRuntimeActivationError() from exc
    except AuthRuntimeActivationError:
        raise
    except Exception as exc:
        log.warning(
            "OpenID Connect provider activation failed: %s",
            type(exc).__name__,
        )
        raise AuthRuntimeActivationError() from exc
    return AuthRuntimeGeneration(
        generation_id=0,
        oidc_enabled=True,
        provider_name=oidc.name,
        account_provider_name=provider_key,
        openid_issuer=openid_issuer,
        client=client,
        configuration_endpoint=oidc.configuration_endpoint,
        cookie_secure=cookie_secure,
        frontend_url=frontend_url,
        session_lifetime=session_lifetime,
    )


async def prepare_auth_runtime_for_overrides(
    overrides: dict,
) -> AuthRuntimeGeneration:
    """Stage a runtime generation from prospective overrides before persistence."""
    config = build_isolated_config(overrides)
    return await build_auth_runtime_generation(config.auth, config.misc)


def commit_auth_runtime_generation(
    prospective: AuthRuntimeGeneration,
) -> AuthRuntimeGeneration:
    """Atomically activate a pre-validated runtime generation."""
    return auth_runtime_store.swap(prospective)


async def initialize_auth_runtime() -> AuthRuntimeGeneration:
    """Initialize/refresh auth runtime from the live config singleton."""
    live = MiraMediaConfig()
    prospective = await build_auth_runtime_generation(live.auth, live.misc)
    return commit_auth_runtime_generation(prospective)


async def activate_auth_runtime_for_overrides(
    overrides: dict,
) -> AuthRuntimeGeneration:
    """Stage, then commit OIDC runtime for the given effective overrides."""
    prospective = await prepare_auth_runtime_for_overrides(overrides)
    return commit_auth_runtime_generation(prospective)


def current_oauth_runtime_generation() -> AuthRuntimeGeneration:
    """Return the request-scoped generation, else the active store generation."""
    bound = _oauth_runtime_ctx.get()
    if bound is not None:
        return bound
    return auth_runtime_store.get_active()


@contextlib.asynccontextmanager
async def bind_oauth_runtime_generation(
    generation: AuthRuntimeGeneration,
) -> AsyncIterator[AuthRuntimeGeneration]:
    """Bind one immutable runtime generation for the current async context."""
    token = _oauth_runtime_ctx.set(generation)
    try:
        yield generation
    finally:
        _oauth_runtime_ctx.reset(token)


@contextlib.asynccontextmanager
async def oauth_runtime_request_scope() -> AsyncIterator[AuthRuntimeGeneration]:
    """Bind one immutable runtime generation for the current async context."""
    captured = auth_runtime_store.get_active()
    token = _oauth_runtime_ctx.set(captured)
    try:
        yield captured
    finally:
        _oauth_runtime_ctx.reset(token)


class OAuthRuntimeMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        async with oauth_runtime_request_scope():
            return await call_next(request)


class DynamicOAuthClient(BaseOAuth2[OAuth2Token]):
    """Stable OAuth boundary mounted once; delegates to the request generation."""

    def __init__(self) -> None:
        super().__init__(
            client_id="disabled",
            client_secret="disabled",  # noqa: S106
            authorize_endpoint="https://disabled.invalid/authorize",
            access_token_endpoint="https://disabled.invalid/token",  # noqa: S106
            name=OAUTH_ROUTE_NAME,
        )

    def _require_client(self) -> OpenID:
        generation = current_oauth_runtime_generation()
        if not generation.oidc_enabled or generation.client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenID Connect is not enabled",
            )
        return generation.client

    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str | None = None,
        scope: list[str] | None = None,
        code_challenge: str | None = None,
        code_challenge_method: Literal["plain", "S256"] | None = None,
        extras_params: OAuth2Token | None = None,
    ) -> str:
        return await self._require_client().get_authorization_url(
            redirect_uri,
            state,
            scope,
            code_challenge,
            code_challenge_method,
            extras_params,
        )

    async def get_access_token(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> OAuth2Token:
        return await self._require_client().get_access_token(
            code, redirect_uri, code_verifier
        )

    async def get_id_email(self, token: str) -> tuple[str, str | None]:
        return await self._require_client().get_id_email(token)

    async def get_profile(self, token: str) -> dict[str, object]:
        return await self._require_client().get_profile(token)


dynamic_oauth_client = DynamicOAuthClient()


def reset_auth_runtime_for_tests() -> AuthRuntimeGeneration:
    """Restore disabled runtime generation (tests only)."""
    return auth_runtime_store.reset_for_tests()

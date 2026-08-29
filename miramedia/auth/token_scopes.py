"""Least-privilege scope vocabulary and enforcement for personal API tokens."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from typing import ParamSpec, TypeVar

from fastapi import HTTPException, Request, status
from starlette.requests import Request as StarletteRequest
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

API_TOKEN_PREFIX = "mm_"  # noqa: S105 -- public prefix, not a secret

SCOPE_LIBRARY_READ = "library:read"
SCOPE_LIBRARY_WRITE = "library:write"
SCOPE_DOWNLOADS_WRITE = "downloads:write"
SCOPE_PLAYBACK_WRITE = "playback:write"
SCOPE_OPS_READ = "ops:read"
SCOPE_SETTINGS_WRITE = "settings:write"

SCOPE_VOCABULARY: frozenset[str] = frozenset(
    {
        SCOPE_LIBRARY_READ,
        SCOPE_LIBRARY_WRITE,
        SCOPE_DOWNLOADS_WRITE,
        SCOPE_PLAYBACK_WRITE,
        SCOPE_OPS_READ,
        SCOPE_SETTINGS_WRITE,
    }
)

TOKEN_SCOPE_PUBLIC = "public"  # noqa: S105
TOKEN_SCOPE_SESSION = "session"  # noqa: S105

SESSION_COOKIE_NAME = "fastapiusersauth"

_current_request: ContextVar[StarletteRequest | None] = ContextVar(
    "miramedia_current_request", default=None
)

P = ParamSpec("P")
R = TypeVar("R")


def classify_auth_via(request: Request) -> str:
    """Classify the inbound credential without a DB round-trip."""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith(API_TOKEN_PREFIX):
            return "api_token"
        return "session"
    if request.cookies.get(SESSION_COOKIE_NAME):
        return "session"
    return "anonymous"


def effective_token_scopes(raw: list[str] | None) -> list[str]:
    """Return only vocabulary-known scopes; unknown stored strings never grant access."""
    if not raw:
        return []
    return [scope for scope in raw if scope in SCOPE_VOCABULARY]


def validate_scope_names(scopes: list[str]) -> list[str]:
    unknown = sorted({scope for scope in scopes if scope not in SCOPE_VOCABULARY})
    if unknown:
        msg = f"Unknown scope(s): {', '.join(unknown)}"
        raise ValueError(msg)
    return scopes


def token_scope(scope: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Attach the declared token scope to a route handler for central enforcement."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        setattr(func, "token_scope", scope)  # noqa: B010
        return func

    return decorator


def get_current_request() -> StarletteRequest | None:
    return _current_request.get()


async def enforce_api_token_scopes(request: Request) -> None:
    """Default-deny scope gate for ``mm_`` bearer credentials after authentication."""
    if getattr(request.state, "auth_via", None) != "api_token":
        return

    endpoint = request.scope.get("endpoint")
    required = getattr(endpoint, "token_scope", None)

    token_id = getattr(request.state, "api_token_id", None)
    preview = getattr(request.state, "api_token_preview", None)
    path = request.url.path
    correlation_id = request.headers.get("X-Correlation-ID")

    if required is None or required == TOKEN_SCOPE_SESSION:
        log.info(
            "API token route denied token_id=%s preview=%s path=%s correlation_id=%s",
            token_id,
            preview,
            path,
            correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This route does not accept API tokens",
        )

    if required == TOKEN_SCOPE_PUBLIC:
        return

    granted = effective_token_scopes(getattr(request.state, "api_token_scopes", None))
    if required not in granted:
        log.info(
            "API token scope denied token_id=%s preview=%s required=%s scopes=%s "
            "path=%s correlation_id=%s",
            token_id,
            preview,
            required,
            granted,
            path,
            correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope",
        )

    log.debug(
        "API token scope ok token_id=%s required=%s path=%s",
        token_id,
        required,
        path,
    )


class BindRequestMiddleware:
    """Bind the active ``Request`` and classify ``auth_via`` for the auth hot path."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = StarletteRequest(scope, receive)
        reset = _current_request.set(request)
        try:
            request.state.auth_via = classify_auth_via(request)
            await self.app(scope, receive, send)
        finally:
            _current_request.reset(reset)


def attach_api_token_state(
    request: StarletteRequest,
    *,
    token_id: uuid.UUID,
    scopes: list[str],
    preview: str,
) -> None:
    request.state.api_token_id = token_id
    request.state.api_token_scopes = list(scopes)
    request.state.api_token_preview = preview

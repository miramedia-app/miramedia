"""Custom OAuth router with request-scoped generation settings."""

# Mirrors fastapi-users oauth router patterns; keep lint parity with upstream.
# ruff: noqa: FAST002, B008, S107

from __future__ import annotations

import inspect
import secrets
from typing import Literal, cast

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi_users import models
from fastapi_users.authentication import AuthenticationBackend, Strategy
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.jwt import SecretType, decode_jwt
from fastapi_users.manager import BaseUserManager, UserManagerDependency
from fastapi_users.router.common import ErrorCode, ErrorModel
from fastapi_users.router.oauth import (
    CSRF_TOKEN_COOKIE_NAME,
    CSRF_TOKEN_KEY,
    STATE_TOKEN_AUDIENCE,
    OAuth2AuthorizeResponse,
    generate_csrf_token,
    generate_state_token,
)
from httpx_oauth.oauth2 import GetAccessTokenError, OAuth2Token

from miramedia.auth.oauth_provider import (
    OAuthProviderConflictError,
    OAuthProviderReconciliationError,
    reconcile_legacy_oauth_account,
)
from miramedia.auth.oauth_state import (
    OAUTH_SNAPSHOT_COOKIE_NAME,
    OAuthAuthorizeSnapshotError,
    auth_runtime_generation_from_snapshot,
    decrypt_oauth_authorize_snapshot,
    encrypt_oauth_authorize_snapshot,
    snapshot_from_generation,
)
from miramedia.auth.runtime import (
    OAUTH_ROUTE_NAME,
    bind_oauth_runtime_generation,
    current_oauth_runtime_generation,
    dynamic_oauth_client,
    get_oauth_id_email_verified,
)


async def _resolve_backend_strategy(
    backend: AuthenticationBackend[models.UP, models.ID],
) -> Strategy[models.UP, models.ID]:
    result = backend.get_strategy()
    if inspect.isawaitable(result):
        return cast(Strategy[models.UP, models.ID], await result)
    return cast(Strategy[models.UP, models.ID], result)


def get_dynamic_oauth_router(
    backend: AuthenticationBackend[models.UP, models.ID],
    get_user_manager: UserManagerDependency[models.UP, models.ID],
    state_secret: SecretType,
    redirect_url: str | None = None,
    associate_by_email: bool = False,
    is_verified_by_default: bool = False,
    *,
    csrf_token_cookie_name: str = CSRF_TOKEN_COOKIE_NAME,
    csrf_token_cookie_path: str = "/",
    csrf_token_cookie_domain: str | None = None,
    csrf_token_cookie_httponly: bool = True,
    csrf_token_cookie_samesite: Literal["lax", "strict", "none"] = "lax",
    oauth_snapshot_cookie_name: str = OAUTH_SNAPSHOT_COOKIE_NAME,
) -> APIRouter:
    """OAuth routes with stable mount names and generation-scoped CSRF/provider identity."""
    router = APIRouter()
    callback_route_name = f"oauth:{OAUTH_ROUTE_NAME}.{backend.name}.callback"

    def _clear_oauth_snapshot_cookie(response: Response) -> None:
        response.delete_cookie(
            oauth_snapshot_cookie_name,
            path=csrf_token_cookie_path,
            domain=csrf_token_cookie_domain,
        )

    @router.get(
        "/authorize",
        name=f"oauth:{OAUTH_ROUTE_NAME}.{backend.name}.authorize",
        response_model=OAuth2AuthorizeResponse,
    )
    async def authorize(
        request: Request, response: Response, scopes: list[str] = Query(None)
    ) -> OAuth2AuthorizeResponse:
        generation = current_oauth_runtime_generation()
        if redirect_url is not None:
            authorize_redirect_url = redirect_url
        else:
            authorize_redirect_url = str(request.url_for(callback_route_name))

        csrf_token = generate_csrf_token()
        try:
            snapshot_token = encrypt_oauth_authorize_snapshot(
                snapshot_from_generation(generation),
                state_secret,
            )
        except OAuthAuthorizeSnapshotError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenID Connect is not enabled",
            ) from exc

        state_data: dict[str, str] = {CSRF_TOKEN_KEY: csrf_token}
        state = generate_state_token(state_data, state_secret)
        authorization_url = await dynamic_oauth_client.get_authorization_url(
            authorize_redirect_url,
            state,
            scopes,
        )

        response.set_cookie(
            csrf_token_cookie_name,
            csrf_token,
            max_age=3600,
            path=csrf_token_cookie_path,
            domain=csrf_token_cookie_domain,
            secure=generation.cookie_secure,
            httponly=csrf_token_cookie_httponly,
            samesite=csrf_token_cookie_samesite,
        )
        response.set_cookie(
            oauth_snapshot_cookie_name,
            snapshot_token,
            max_age=3600,
            path=csrf_token_cookie_path,
            domain=csrf_token_cookie_domain,
            secure=generation.cookie_secure,
            httponly=csrf_token_cookie_httponly,
            samesite=csrf_token_cookie_samesite,
        )

        return OAuth2AuthorizeResponse(authorization_url=authorization_url)

    @router.get(
        "/callback",
        name=callback_route_name,
        description="The response varies based on the authentication backend used.",
        responses={
            status.HTTP_400_BAD_REQUEST: {
                "model": ErrorModel,
                "content": {
                    "application/json": {
                        "examples": {
                            "INVALID_STATE_TOKEN": {
                                "summary": "Invalid state token.",
                                "value": None,
                            },
                            ErrorCode.LOGIN_BAD_CREDENTIALS: {
                                "summary": "User is inactive.",
                                "value": {"detail": ErrorCode.LOGIN_BAD_CREDENTIALS},
                            },
                        }
                    }
                },
            },
        },
    )
    async def callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        user_manager: BaseUserManager[models.UP, models.ID] = Depends(get_user_manager),
    ) -> Response:
        async def _run_callback() -> Response:
            if code is None or error is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error
                    if error is not None
                    else ErrorCode.OAUTH_INVALID_STATE,
                )
            if state is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.OAUTH_INVALID_STATE,
                )

            try:
                state_data = decode_jwt(state, state_secret, [STATE_TOKEN_AUDIENCE])
            except jwt.DecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.ACCESS_TOKEN_DECODE_ERROR,
                ) from None
            except jwt.ExpiredSignatureError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.ACCESS_TOKEN_ALREADY_EXPIRED,
                ) from None

            snapshot_token = request.cookies.get(oauth_snapshot_cookie_name)
            if not snapshot_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.OAUTH_INVALID_STATE,
                )

            try:
                authorize_snapshot = decrypt_oauth_authorize_snapshot(
                    snapshot_token,
                    state_secret,
                )
                generation = await auth_runtime_generation_from_snapshot(
                    authorize_snapshot
                )
            except OAuthAuthorizeSnapshotError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.OAUTH_INVALID_STATE,
                ) from exc

            if redirect_url is not None:
                callback_redirect_url = redirect_url
            else:
                callback_redirect_url = str(request.url_for(callback_route_name))

            async with bind_oauth_runtime_generation(generation):
                cookie_csrf_token = request.cookies.get(csrf_token_cookie_name)
                state_csrf_token = state_data.get(CSRF_TOKEN_KEY)
                if (
                    not cookie_csrf_token
                    or not state_csrf_token
                    or not secrets.compare_digest(cookie_csrf_token, state_csrf_token)
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ErrorCode.OAUTH_INVALID_STATE,
                    )

                if generation.client is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ErrorCode.OAUTH_INVALID_STATE,
                    )

                try:
                    token: OAuth2Token = await generation.client.get_access_token(
                        code,
                        callback_redirect_url,
                    )
                except GetAccessTokenError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=ErrorCode.OAUTH_INVALID_STATE,
                    ) from exc

                (
                    account_id,
                    account_email,
                    email_verified,
                ) = await get_oauth_id_email_verified(
                    generation.client,
                    token["access_token"],
                )

                if account_email is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ErrorCode.OAUTH_NOT_AVAILABLE_EMAIL,
                    )

                effective_associate_by_email = associate_by_email and email_verified
                effective_is_verified_by_default = (
                    is_verified_by_default and email_verified
                )

                try:
                    await reconcile_legacy_oauth_account(
                        user_manager.user_db,
                        account_id=str(account_id),
                        display_name=generation.provider_name,
                        provider_key=generation.account_provider_name,
                    )
                except (
                    OAuthProviderConflictError,
                    OAuthProviderReconciliationError,
                ) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ErrorCode.OAUTH_INVALID_STATE,
                    ) from exc

                provider_name = generation.account_provider_name
                try:
                    user = await user_manager.oauth_callback(  # ty: ignore[invalid-argument-type]
                        str(provider_name),
                        token["access_token"],
                        account_id,
                        account_email,
                        token.get("expires_at"),
                        token.get("refresh_token"),
                        request,
                        associate_by_email=effective_associate_by_email,
                        is_verified_by_default=effective_is_verified_by_default,
                    )
                except UserAlreadyExists:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ErrorCode.OAUTH_USER_ALREADY_EXISTS,
                    ) from None

                if not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=ErrorCode.LOGIN_BAD_CREDENTIALS,
                    )

                strategy = await _resolve_backend_strategy(backend)
                login_response = await backend.login(strategy, user)
                await user_manager.on_after_login(user, request, login_response)
                return login_response

        try:
            response = await _run_callback()
        except HTTPException as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        _clear_oauth_snapshot_cookie(response)
        return response

    return router

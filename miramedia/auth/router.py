import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from miramedia.auth.api_tokens import UserApiToken, generate_token
from miramedia.auth.db import User
from miramedia.auth.oauth_router import get_dynamic_oauth_router
from miramedia.auth.runtime import auth_runtime_store
from miramedia.auth.schemas import AuthMetadata, UserRead
from miramedia.auth.token_scopes import (
    SCOPE_DOWNLOADS_WRITE,
    SCOPE_LIBRARY_READ,
    SCOPE_LIBRARY_WRITE,
    SCOPE_OPS_READ,
    SCOPE_PLAYBACK_WRITE,
    SCOPE_SETTINGS_WRITE,
    SCOPE_VOCABULARY,
    TOKEN_SCOPE_SESSION,
    token_scope,
    validate_scope_names,
)
from miramedia.auth.users import (
    SECRET,
    CurrentInteractiveUserDep,
    CurrentUserDep,
    SuperuserDep,
    current_superuser,
    fastapi_users,
    invalidate_auth_cache,
    openid_cookie_auth_backend,
)
from miramedia.config import MiraMediaConfig
from miramedia.database import DbSessionDependency

log = logging.getLogger(__name__)

users_router = APIRouter(tags=["users"])
auth_metadata_router = APIRouter(tags=["openid"])


def get_openid_router() -> APIRouter:
    return get_dynamic_oauth_router(
        backend=openid_cookie_auth_backend,
        get_user_manager=fastapi_users.get_user_manager,
        state_secret=SECRET,
        associate_by_email=True,
        is_verified_by_default=True,
        redirect_url=None,
    )


@users_router.get(
    "/users",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def get_all_users(db: DbSessionDependency) -> list[UserRead]:
    stmt = select(User)
    result = (await db.execute(stmt)).scalars().unique()
    return [UserRead.model_validate(user) for user in result]


@auth_metadata_router.get("/auth/metadata", status_code=status.HTTP_200_OK)
def get_auth_metadata() -> AuthMetadata:
    allow_registration = MiraMediaConfig().auth.allow_registration
    generation = auth_runtime_store.get_active()
    if generation.oidc_enabled:
        return AuthMetadata(
            oauth_providers=[generation.provider_name],
            allow_registration=allow_registration,
        )
    return AuthMetadata(
        oauth_providers=[],
        allow_registration=allow_registration,
    )


# --- Personal API tokens -----------------------------------------------------
#
# Credential boundary: creating or revoking tokens requires an interactive session
# (JWT bearer or cookie). Listing tokens is read-only metadata and still accepts
# API tokens via ``CurrentUserDep``.


class ApiTokenRead(BaseModel):
    id: uuid.UUID
    name: str
    preview: str  # last 4 chars of the plaintext token, for disambiguation only
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            msg = "expires_at must be timezone-aware with a UTC offset"
            raise ValueError(msg)
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        return validate_scope_names(value)


# OpenAPI vocabulary for token mint UI (frozen set from design 388).
API_TOKEN_SCOPE_NAMES = sorted(SCOPE_VOCABULARY)
API_TOKEN_SCOPE_LABELS: dict[str, str] = {
    SCOPE_LIBRARY_READ: "Library read (catalog, queue, notifications)",
    SCOPE_LIBRARY_WRITE: "Library write (add, skip, watchlists, requests)",
    SCOPE_DOWNLOADS_WRITE: "Downloads write (search, start/control torrents, imports)",
    SCOPE_PLAYBACK_WRITE: "Playback write (own progress and watched state)",
    SCOPE_OPS_READ: "Ops read (health details, logs, updates, metrics)",
    SCOPE_SETTINGS_WRITE: "Settings write (config and indexer CRUD)",
}


class ApiTokenCreated(ApiTokenRead):
    """Response on creation — includes the plaintext token, shown to the user once."""

    token: str


@users_router.get(
    "/users/me/tokens",
    status_code=status.HTTP_200_OK,
)
@token_scope(TOKEN_SCOPE_SESSION)
async def list_my_tokens(
    db: DbSessionDependency,
    user: CurrentUserDep,
) -> list[ApiTokenRead]:
    rows = (
        (
            await db.execute(
                select(UserApiToken)
                .where(UserApiToken.user_id == user.id)
                .order_by(UserApiToken.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ApiTokenRead.model_validate(row, from_attributes=True) for row in rows]


@users_router.post(
    "/users/me/tokens",
    status_code=status.HTTP_201_CREATED,
)
async def create_my_token(
    data: ApiTokenCreate,
    db: DbSessionDependency,
    user: CurrentInteractiveUserDep,
) -> ApiTokenCreated:
    if data.expires_at is not None and data.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expires_at must be in the future",
        )
    plaintext, token_hash, preview = generate_token()
    row = UserApiToken(
        user_id=user.id,
        name=data.name,
        token_hash=token_hash,
        preview=preview,
        scopes=data.scopes,
        expires_at=data.expires_at,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return ApiTokenCreated(
        id=row.id,
        name=row.name,
        preview=row.preview,
        scopes=row.scopes,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        token=plaintext,
    )


@users_router.delete(
    "/users/me/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@token_scope(TOKEN_SCOPE_SESSION)
async def revoke_my_token(
    token_id: uuid.UUID,
    db: DbSessionDependency,
    user: CurrentInteractiveUserDep,
) -> None:
    row = await db.get(UserApiToken, token_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )
    await db.delete(row)


# --- Admin: trigger password reset --------------------------------------------------


@users_router.post(
    "/users/{user_id}/password-reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(current_superuser)],
)
async def admin_trigger_password_reset(user_id: uuid.UUID) -> None:
    """Send a password reset email to the targeted user.

    Reuses the existing ``UserManager.forgot_password`` flow so the reset link, token
    expiry, and email template are identical to the user-initiated path. Superuser only.
    """
    from miramedia.auth.users import (  # local import avoids circular dep at import
        get_async_session_context,
        get_user_db_context,
        get_user_manager_context,
    )

    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                target = await user_db.get(user_id)
                if target is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
                    )
                await user_manager.forgot_password(target)


# --- Admin: bulk user updates -------------------------------------------------------


class BulkUserUpdate(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1)
    is_active: bool | None = None


class BulkUserUpdateResult(BaseModel):
    updated: int


@users_router.post(
    "/users/bulk",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def admin_bulk_user_update(
    body: BulkUserUpdate,
    db: DbSessionDependency,
    actor: SuperuserDep,
) -> BulkUserUpdateResult:
    """Apply an active/inactive flag to many users at once.

    The acting superuser is filtered out so they can't lock themselves out by
    deactivating their own account in a bulk sweep.
    """
    if body.is_active is None:
        return BulkUserUpdateResult(updated=0)

    from sqlalchemy import update as sa_update

    target_ids = [uid for uid in body.user_ids if uid != actor.id]
    if not target_ids:
        return BulkUserUpdateResult(updated=0)

    result = await db.execute(
        sa_update(User).where(User.id.in_(target_ids)).values(is_active=body.is_active)
    )
    await db.commit()
    # Bulk update bypasses ``UserManager.on_after_update``; drop cached auth
    # state for the affected users so deactivation takes effect immediately.
    for uid in target_ids:
        invalidate_auth_cache(uid)
    return BulkUserUpdateResult(updated=result.rowcount or 0)


# --- Admin: invite by email --------------------------------------------------------


class InviteUserRequest(BaseModel):
    email: str
    is_superuser: bool = False


class InviteUserResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    invite_email_sent: bool


@users_router.post(
    "/users/invite",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(current_superuser)],
)
async def admin_invite_user(body: InviteUserRequest) -> InviteUserResponse:
    """Create a pending user with a random password and email them a reset link.

    Reuses ``forgot_password`` so the recipient can pick their own password through
    the existing reset flow. Returns the new user id; the email is best-effort.
    """
    import secrets as _secrets

    from sqlalchemy.exc import IntegrityError

    from miramedia.auth.schemas import UserCreate
    from miramedia.auth.users import (
        get_async_session_context,
        get_user_db_context,
        get_user_manager_context,
    )

    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                try:
                    new_user = await user_manager.create(
                        UserCreate(
                            email=body.email,
                            password=_secrets.token_urlsafe(16),
                            is_active=True,
                            is_verified=True,
                            is_superuser=body.is_superuser,
                        )
                    )
                except IntegrityError as exc:
                    raise HTTPException(
                        status_code=409, detail="A user with that email already exists"
                    ) from exc
                except Exception as exc:
                    log.exception("Admin invite failed for %s", body.email)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Could not create user",
                    ) from exc

                email_sent = True
                try:
                    await user_manager.forgot_password(new_user)
                except Exception:
                    email_sent = False
                return InviteUserResponse(
                    user_id=new_user.id,
                    email=new_user.email,
                    invite_email_sent=email_sent,
                )


# --- Admin: create user directly --------------------------------------------------


class CreateUserRequest(BaseModel):
    email: str
    password: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = True


@users_router.post(
    "/users/create",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(current_superuser)],
)
async def admin_create_user(body: CreateUserRequest) -> User:
    """Create a user honoring the active/verified/superuser flags.

    The built-in ``/auth/register`` route calls ``user_manager.create`` with
    ``safe=True``, which silently strips ``is_active``/``is_superuser``/
    ``is_verified`` from the payload. Admin creation must respect those switches,
    so this endpoint creates with the default ``safe=False`` (same as invite).
    """
    from sqlalchemy.exc import IntegrityError

    from miramedia.auth.schemas import UserCreate
    from miramedia.auth.users import (
        get_async_session_context,
        get_user_db_context,
        get_user_manager_context,
    )

    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                try:
                    return await user_manager.create(
                        UserCreate(
                            email=body.email,
                            password=body.password,
                            is_active=body.is_active,
                            is_verified=body.is_verified,
                            is_superuser=body.is_superuser,
                        )
                    )
                except IntegrityError as exc:
                    raise HTTPException(
                        status_code=409, detail="A user with that email already exists"
                    ) from exc
                except Exception as exc:
                    log.exception("Admin create failed for %s", body.email)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Could not create user",
                    ) from exc

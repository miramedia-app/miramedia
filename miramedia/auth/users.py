import contextlib
import hashlib
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any, override

import jwt
from cachetools import TTLCache
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, models
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users.jwt import generate_jwt
from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import make_transient
from starlette import status

import miramedia.notifications.utils
from miramedia.auth.db import OAuthAccount, User, get_user_db
from miramedia.auth.runtime import (
    current_oauth_runtime_generation,
    get_live_auth_config,
    join_frontend_path,
)
from miramedia.auth.schemas import UserCreate, UserUpdate
from miramedia.config import MiraMediaConfig
from miramedia.database import get_session

log = logging.getLogger(__name__)

# Restart-only: token signing secrets are captured at process start.
SECRET = MiraMediaConfig().auth.token_secret


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    @override
    async def validate_password(
        self, password: str, user: models.UP | UserCreate
    ) -> None:
        if len(password) < 8:
            raise InvalidPasswordException(
                reason="Password must be at least 8 characters"
            )
        email = getattr(user, "email", None)
        if email and password.lower() == str(email).lower():
            raise InvalidPasswordException(
                reason="Password must not be the same as the email address"
            )

    @override
    async def on_after_update(
        self,
        user: models.UP,
        update_dict: dict[str, Any],
        request: Request | None = None,
    ) -> None:
        log.info(f"User {user.id} has been updated.")
        if update_dict.get("is_superuser"):
            log.info(f"User {user.id} has been granted superuser privileges.")
        # Drop cached auth state so role/active/email changes take effect on the
        # next request instead of waiting out the TTL window.
        invalidate_auth_cache(user.id)
        if "password" in update_dict:
            await self._revoke_credentials(user, reason="has changed their password")
        if "email" in update_dict:
            updated_user = UserUpdate(is_verified=True)
            await self.update(user=user, user_update=updated_user)

    async def _revoke_credentials(self, user: models.UP, *, reason: str) -> None:
        """Stamp ``credentials_changed_at`` and delete all personal API tokens.

        Invalidates every JWT minted more than ``_IAT_LEEWAY_S`` before now
        (via ``_issued_before_credentials_change``) — including the user's own
        current session, which is intentional: after a password change/reset
        the user must log in again.
        """
        from datetime import UTC
        from datetime import datetime as _dt

        from sqlalchemy import delete as sa_delete

        from miramedia.auth.api_tokens import UserApiToken

        now = _dt.now(UTC)
        await self.user_db.update(user, {"credentials_changed_at": now})
        session = self.user_db.session
        await session.execute(
            sa_delete(UserApiToken).where(UserApiToken.user_id == user.id)
        )
        await session.commit()
        invalidate_auth_cache(user.id)
        log.info(f"User {user.id} {reason}; sessions and API tokens revoked.")

    @override
    async def on_after_register(
        self, user: User, request: Request | None = None
    ) -> None:
        log.info(f"User {user.id} has registered.")
        if user.email in get_live_auth_config().admin_emails:
            updated_user = UserUpdate(is_superuser=True, is_verified=True)
            await self.update(user=user, user_update=updated_user)

    @override
    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        link = f"{MiraMediaConfig().misc.frontend_url}web/login/reset-password?token={token}"
        log.info(f"User {user.id} requested a password reset.")

        if not get_live_auth_config().email_password_resets:
            log.warning(
                "Email password resets are disabled; no reset email was sent for user %s",
                user.id,
            )
            return

        subject = "MiraMedia - Password Reset Request"
        html = f"""\
        <html>
          <body>
            <p>Hi {user.email},
            <br>
            <br>
            if you forgot your password, <a href=\"{link}\">reset you password here</a>.<br>
            If you did not request a password reset, you can ignore this email.</p>
            <br>
            <br>
            If the link does not work, copy the following link into your browser: {link}<br>
          </body>
        </html>
        """
        miramedia.notifications.utils.send_email(
            subject=subject, html=html, addressee=user.email
        )
        log.info(f"Sent password reset email to {user.email}")

    @override
    async def on_after_reset_password(
        self, user: User, request: Request | None = None
    ) -> None:
        await self._revoke_credentials(user, reason="has reset their password")

    @override
    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        log.info(f"Verification requested for user {user.id}")

    @override
    async def on_after_verify(self, user: User, request: Request | None = None) -> None:
        log.info(f"User {user.id} has been verified")

    @override
    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        from datetime import UTC
        from datetime import datetime as _dt

        try:
            await self.user_db.update(user, {"last_login_at": _dt.now(UTC)})
        except Exception:
            log.exception("Failed to update last_login_at for user %s", user.id)


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase, Depends(get_user_db)],
) -> AsyncGenerator[UserManager]:
    yield UserManager(user_db)


get_async_session_context = contextlib.asynccontextmanager(get_session)
get_user_db_context = contextlib.asynccontextmanager(get_user_db)
get_user_manager_context = contextlib.asynccontextmanager(get_user_manager)


async def migrate_admin_emails_to_superuser_flag() -> None:
    """Promote any users whose email matches ``auth.admin_emails`` to ``is_superuser=True``.

    ``admin_emails`` is deprecated as a long-lived superuser source; the canonical superuser
    flag now lives on the user row. This runs at startup so existing installations stay
    compatible while we migrate to per-user flags. Logs a deprecation warning when any
    addresses are listed.
    """
    cfg = MiraMediaConfig().auth
    emails = [e.strip().lower() for e in (cfg.admin_emails or []) if e and e.strip()]
    if not emails:
        return

    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from miramedia.auth.db import User as UserModel
    from miramedia.database import SessionLocal

    async with SessionLocal() as db:
        stmt = sa_select(UserModel.id, UserModel.email, UserModel.is_superuser).where(
            UserModel.email.in_(emails)
        )
        rows = (await db.execute(stmt)).all()
        promoted = [row.email for row in rows if not row.is_superuser]
        if promoted:
            await db.execute(
                sa_update(UserModel)
                .where(UserModel.email.in_(promoted))
                .values(is_superuser=True)
            )
            await db.commit()
            log.info(
                "Promoted %d user(s) to superuser via deprecated admin_emails: %s",
                len(promoted),
                ", ".join(promoted),
            )

    log.warning(
        "auth.admin_emails is deprecated. Existing users matching this list have been "
        "promoted to superuser. Manage superuser status from the Users page going forward; "
        "remove admin_emails from your config to silence this warning."
    )


async def create_default_admin_user() -> None:
    """Create a default admin user if no users exist in the database"""
    try:
        async with get_async_session_context() as session:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    # Check if any users exist
                    stmt = select(func.count(User.id))
                    result = await session.execute(stmt)
                    user_count = result.scalar()
                    config = MiraMediaConfig()
                    if user_count == 0:
                        log.info(
                            "No users found in database. Creating default admin user..."
                        )

                        # Use the first admin email from config, or default
                        admin_email = (
                            config.auth.admin_emails[0]
                            if config.auth.admin_emails
                            else "admin@example.com"
                        )
                        default_password = "admin"  # noqa: S105

                        user_create = UserCreate(
                            email=admin_email,
                            password=default_password,
                            is_superuser=True,
                            is_verified=True,
                        )

                        user = await user_manager.create(user_create)
                        log.info("=" * 60)
                        log.info("DEFAULT ADMIN USER CREATED!")
                        log.info(f"    Email: {admin_email}")
                        log.info(f"    Password: {default_password}")
                        log.info(f"    User ID: {user.id}")
                        log.info("IMPORTANT: Change this password after login.")
                        log.info("=" * 60)

                    else:
                        log.info(
                            f"Found {user_count} existing users. Skipping default user creation."
                        )
    except Exception:
        log.exception("Failed to create default admin user")
        log.info(
            "You can create an admin user manually by registering with an email from the admin_emails list in your config."
        )


# ---------------------------------------------------------------------------
# Cached JWT strategy
#
# Auth is on the hot path of *every* authenticated request: the existing
# ``current_active_user`` chain decodes the JWT, then runs a DB ``user.get(id)``
# round-trip. On a small NAS deployment this was the dominant per-request cost
# (Round-3 load test saw the app saturate well under available CPU). We cache
# the decoded user by ``sha256(token)`` for a short TTL so subsequent calls
# within a burst skip both the decode work and the DB hit. Revocation latency
# is bounded by the TTL (default 30s) and explicit invalidation via
# ``invalidate_auth_cache`` on role/active changes.
#
# This wraps the JWT strategy only — opaque API tokens (``mm_*``) go through
# ``DatabaseTokenStrategy``, which already does its own minimal lookup and runs
# a write to update ``last_used_at``, so caching there would hide audit signal.
# ---------------------------------------------------------------------------
_AUTH_CACHE_TTL_S = int(os.getenv("MIRAMEDIA_AUTH_CACHE_TTL_SECONDS", "30"))
_AUTH_CACHE_MAX = int(os.getenv("MIRAMEDIA_AUTH_CACHE_MAXSIZE", "2048"))
_IAT_LEEWAY_S = 60
_AuthCacheEntry = tuple[models.UP, float | None, float | None]
_user_cache: TTLCache[bytes, _AuthCacheEntry] = TTLCache(
    maxsize=_AUTH_CACHE_MAX, ttl=_AUTH_CACHE_TTL_S
)


def _token_cache_key(token: str | None) -> bytes | None:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8", "ignore")).digest()


def _token_exp_epoch(token: str) -> float | None:
    """Return the JWT ``exp`` claim without verifying the signature.

    Only call on tokens the parent ``JWTStrategy`` has already verified on a
    cache miss; the cache key is the full token hash so forged tokens cannot
    seed entries.
    """
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        return None
    exp = payload.get("exp")
    return float(exp) if exp is not None else None


def _token_iat_epoch(token: str) -> float | None:
    """Return the JWT ``iat`` claim without verifying the signature."""
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        return None
    iat = payload.get("iat")
    return float(iat) if iat is not None else None


def _issued_before_credentials_change(iat_epoch: float | None, user: models.UP) -> bool:
    changed_at = getattr(user, "credentials_changed_at", None)
    if changed_at is None:
        return False
    threshold = changed_at.timestamp() - _IAT_LEEWAY_S
    if iat_epoch is None:
        return True
    return iat_epoch < threshold


def _detached_user_copy(user: User) -> User:
    """Return a session-independent copy of ``user`` that is safe to cache.

    The instance returned by the user manager stays bound to the request's DB
    session. Once that session commits, rolls back (the error path in
    ``get_session``), or closes, the live instance detaches and its column
    attributes may be expired. Reading e.g. ``is_active`` on a later cache hit
    then triggers a lazy refresh against a dead session, raising
    ``DetachedInstanceError``. We copy the already-loaded column values (and the
    eager ``oauth_accounts`` rows) into fresh transient instances that belong to
    no session and can never lazy-load.
    """
    snapshot = User()
    for col in sa_inspect(User).columns.keys():
        setattr(snapshot, col, getattr(user, col))
    accounts: list[OAuthAccount] = []
    for account in user.oauth_accounts:
        copy = OAuthAccount()
        for col in sa_inspect(OAuthAccount).columns.keys():
            setattr(copy, col, getattr(account, col))
        make_transient(copy)
        accounts.append(copy)
    snapshot.oauth_accounts = accounts
    make_transient(snapshot)
    return snapshot


def invalidate_auth_cache(user_id: uuid.UUID | None = None) -> None:
    """Clear cached user lookups for one user (or everyone).

    Call after any change that should propagate immediately: role flips,
    deactivation, deletion, password reset. The TTL itself bounds staleness to
    ~30s; this helper exists for changes that must be visible right away.
    """
    if user_id is None:
        _user_cache.clear()
        return
    for k, (cached_user, _exp, _iat) in list(_user_cache.items()):
        if getattr(cached_user, "id", None) == user_id:
            _user_cache.pop(k, None)


class CachedJWTStrategy(JWTStrategy[models.UP, models.ID]):
    """``JWTStrategy`` with a small TTL cache keyed by token signature.

    On a cache hit we skip both the JWT decode and the ``user_manager.get(id)``
    round-trip, but still reject entries whose stored ``exp`` has passed. On a
    miss we fall back to the parent implementation and store the resulting
    user. ``None`` results are intentionally not cached so a momentary DB
    failure doesn't poison auth for the TTL window.
    """

    @override
    async def write_token(self, user: models.UP) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "iat": int(time.time()),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    @override
    async def read_token(
        self,
        token: str | None,
        user_manager: BaseUserManager[models.UP, models.ID],
    ) -> models.UP | None:
        key = _token_cache_key(token)
        if key is not None:
            cached = _user_cache.get(key)
            if cached is not None:
                cached_user, exp_epoch, iat_epoch = cached
                if exp_epoch is not None and time.time() >= exp_epoch:
                    _user_cache.pop(key, None)
                elif _issued_before_credentials_change(iat_epoch, cached_user):
                    _user_cache.pop(key, None)
                else:
                    return cached_user
        user = await super().read_token(token, user_manager)
        if user is not None and key is not None and token is not None:
            iat = _token_iat_epoch(token)
            if _issued_before_credentials_change(iat, user):
                return None
            _user_cache[key] = (
                _detached_user_copy(user),
                _token_exp_epoch(token),
                iat,
            )
        return user


def get_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
    return CachedJWTStrategy(
        secret=SECRET,
        lifetime_seconds=MiraMediaConfig().auth.session_lifetime,
    )


def get_openid_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
    generation = current_oauth_runtime_generation()
    return CachedJWTStrategy(
        secret=SECRET,
        lifetime_seconds=generation.session_lifetime,
    )


class _LiveLifetimeCookieTransport(CookieTransport):
    """CookieTransport that reads ``cookie_max_age`` fresh from the config singleton on every login.

    Lets ``auth.session_lifetime`` updates take effect without a process restart.
    """

    @property
    def cookie_max_age(self) -> int:  # type: ignore[override]
        return MiraMediaConfig().auth.session_lifetime

    @cookie_max_age.setter
    def cookie_max_age(self, _value: int | None) -> None:
        # Ignore writes from base __init__; value is sourced live from config.
        pass


# needed because the default CookieTransport does not redirect after login,
# thus the user would be stuck on the OAuth Providers "redirecting" page
class GenerationScopedRedirectingCookieTransport(CookieTransport):
    """OAuth login transport reading cookie policy from the request generation."""

    def __init__(self) -> None:
        super().__init__(cookie_samesite="lax", cookie_secure=False)

    def _set_login_cookie(self, response: Response, token: str) -> Response:
        generation = current_oauth_runtime_generation()
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=generation.session_lifetime,
            path=self.cookie_path,
            domain=self.cookie_domain,
            secure=generation.cookie_secure,
            httponly=self.cookie_httponly,
            samesite=self.cookie_samesite,
        )
        return response

    async def get_login_response(self, token: str) -> Response:
        generation = current_oauth_runtime_generation()
        redirect = join_frontend_path(generation.frontend_url, "web/dashboard")
        response = RedirectResponse(redirect, status_code=status.HTTP_302_FOUND)
        return self._set_login_cookie(response, token)


def _cookie_secure(config: MiraMediaConfig | None = None) -> bool:
    cfg = config if config is not None else MiraMediaConfig()
    if cfg.auth.cookie_secure is not None:
        return cfg.auth.cookie_secure
    return str(cfg.misc.frontend_url).startswith("https://")


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
cookie_transport = _LiveLifetimeCookieTransport(
    cookie_samesite="lax", cookie_secure=_cookie_secure()
)
openid_cookie_transport = GenerationScopedRedirectingCookieTransport()


def apply_mutable_transport_settings() -> None:
    """Refresh cookie transport flags from the live config singleton."""
    secure = _cookie_secure()
    restore_mutable_transport_settings(secure)


def restore_mutable_transport_settings(secure: bool) -> None:
    cookie_transport.cookie_secure = secure


bearer_auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)
cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)
openid_cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=openid_cookie_transport,
    get_strategy=get_openid_jwt_strategy,
)


# Personal API tokens — stored hashed in the DB and authenticated via the same
# ``Authorization: Bearer ...`` header. Adding the backend to ``fastapi_users`` lets
# every existing ``current_active_user``/``current_superuser`` dependency accept tokens
# transparently. No login route is mounted for this backend (tokens are minted via the
# ``/users/me/tokens`` endpoint).
def get_api_token_strategy() -> Any:  # noqa: ANN401 -- avoids circular import on the strategy class
    # Imported lazily to avoid a circular import (api_tokens references the User model
    # which lives alongside this module).
    from miramedia.auth.api_tokens import DatabaseTokenStrategy

    return DatabaseTokenStrategy()


api_token_bearer_transport = BearerTransport(tokenUrl="users/me/tokens")
api_token_auth_backend = AuthenticationBackend(
    name="api_token",
    transport=api_token_bearer_transport,
    get_strategy=get_api_token_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [bearer_auth_backend, cookie_auth_backend, api_token_auth_backend],
)

current_active_user = fastapi_users.current_user(active=True, verified=True)
current_superuser = fastapi_users.current_user(
    active=True, verified=True, superuser=True
)

CurrentUserDep = Annotated[User, Depends(current_active_user)]
SuperuserDep = Annotated[User, Depends(current_superuser)]


async def require_can_add_media(user: CurrentUserDep) -> User:
    """Allow direct media add only when it can't bypass approval.

    With the request/approval system enabled, non-superusers must submit a
    request (which an admin approves) rather than adding + auto-downloading
    media directly. Superusers always may; when requests are disabled the
    app is in all-users-add mode so anyone active may.
    """
    if user.is_superuser:
        return user
    from fastapi import HTTPException, status

    from miramedia.config import MiraMediaConfig

    if MiraMediaConfig().requests.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct add is disabled; submit a request for approval.",
        )
    return user


CanAddMediaDep = Annotated[User, Depends(require_can_add_media)]

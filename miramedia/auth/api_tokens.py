"""Personal API tokens for headless / scripted use of the MiraMedia API.

Tokens are opaque random strings prefixed with ``mm_`` so they're easy to spot in logs.
The plaintext value is shown to the user once on creation; only a SHA-256 hash is
stored. Each successful authentication updates ``last_used_at`` so users can spot
unused/stale tokens.

Auth is wired into fastapi-users as an additional ``AuthenticationBackend`` whose
``Strategy`` resolves the bearer token via DB lookup. Adding it to ``FastAPIUsers``'
backend list lets every existing ``current_active_user``/``current_superuser`` dependency
accept these tokens transparently — no per-route changes needed.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from fastapi_users.authentication.strategy import Strategy
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base

if TYPE_CHECKING:
    from fastapi_users.manager import BaseUserManager

    from miramedia.auth.db import User

log = logging.getLogger(__name__)

TOKEN_PREFIX = "mm_"  # noqa: S105 -- public prefix, not a secret
TOKEN_BYTES = 24  # 24 bytes -> 32-char base64url body (≈192 bits of entropy)
_LAST_USED_WRITE_INTERVAL_S = 60


class UserApiToken(Base):
    """A personal API token issued by a user for headless API access.

    ``token_hash`` is SHA-256 of the plaintext token; the plaintext is never stored.
    ``preview`` keeps the first/last few characters of the plaintext so users can tell
    multiple tokens apart in the UI without exposing the full secret.
    """

    __tablename__ = "user_api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(length=120), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(length=64), nullable=False, unique=True, index=True
    )
    preview: Mapped[str] = mapped_column(String(length=16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(  # noqa: UP045
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(  # noqa: UP045
        DateTime(timezone=True), nullable=True
    )


def hash_token(plaintext: str) -> str:
    """Hash a token for storage. SHA-256 is sufficient — token is full-entropy random."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Generate a new plaintext token, its hash, and a short preview suffix.

    Returns ``(plaintext, token_hash, preview)``. Show ``plaintext`` to the user once;
    only persist ``token_hash`` and ``preview``.
    """
    body = secrets.token_urlsafe(TOKEN_BYTES)
    plaintext = f"{TOKEN_PREFIX}{body}"
    return plaintext, hash_token(plaintext), plaintext[-4:]


class DatabaseTokenStrategy(Strategy):
    """fastapi-users ``Strategy`` that resolves opaque API tokens via DB lookup.

    Returns ``None`` for every token that doesn't start with ``mm_`` so the JWT strategy
    keeps handling normal session/auth bearer tokens.
    """

    async def read_token(  # type: ignore[override]
        self,
        token: str | None,
        user_manager: BaseUserManager,  # noqa: ARG002 -- required by Strategy interface
    ) -> User | None:
        if not token or not token.startswith(TOKEN_PREFIX):
            return None

        from sqlalchemy import select as sa_select
        from sqlalchemy import update as sa_update

        from miramedia.auth.db import User as UserModel
        from miramedia.database import SessionLocal

        if SessionLocal is None:
            log.error("DatabaseTokenStrategy used before init_engine()")
            return None

        token_hash = hash_token(token)
        now = datetime.now(UTC)
        async with SessionLocal() as db:
            row = (
                await db.execute(
                    sa_select(
                        UserApiToken.id,
                        UserApiToken.user_id,
                        UserApiToken.expires_at,
                        UserApiToken.last_used_at,
                    ).where(UserApiToken.token_hash == token_hash)
                )
            ).first()
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < now:
                return None
            stale = (
                row.last_used_at is None
                or (now - row.last_used_at).total_seconds()
                >= _LAST_USED_WRITE_INTERVAL_S
            )
            if stale:
                await db.execute(
                    sa_update(UserApiToken)
                    .where(UserApiToken.id == row.id)
                    .values(last_used_at=now)
                )
                await db.commit()
            user = await db.get(UserModel, row.user_id)
            if user is None or not user.is_active:
                return None
            # Detach so callers don't trip lazy-load on a closed session.
            # The User row is plain SQLAlchemy mapped data; ``oauth_accounts``
            # uses ``lazy="joined"`` so it's already loaded.
            db.expunge(user)
            return user

    async def write_token(self, user: User) -> str:  # type: ignore[override]  # noqa: ARG002, RUF100
        msg = "API tokens are issued via /users/me/tokens, not the auth login flow."
        raise NotImplementedError(msg)

    async def destroy_token(self, token: str, user: User) -> None:  # type: ignore[override]  # noqa: ARG002
        # Logout/destroy is a no-op for API tokens; users revoke them via the tokens API.
        return None

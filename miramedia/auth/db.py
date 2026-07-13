from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
    SQLAlchemyUserDatabase,
)
from sqlalchemy import DateTime, Index, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from miramedia.database import Base, get_session


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    __table_args__ = (
        Index(
            "uq_oauth_account_oauth_name_account_id",
            "oauth_name",
            "account_id",
            unique=True,
        ),
    )

    access_token: Mapped[str] = mapped_column(String(length=4096), nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(
        String(length=4096), nullable=True
    )


class User(SQLAlchemyBaseUserTableUUID, Base):
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount", lazy="joined"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


async def get_user_db(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncGenerator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)

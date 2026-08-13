"""Seed a disposable verified superuser for the real-browser smoke path."""

from __future__ import annotations

import secrets
import uuid

from miramedia.auth.schemas import UserCreate
from miramedia.auth.users import (
    get_async_session_context,
    get_user_db_context,
    get_user_manager_context,
)
from miramedia.config import MiraMediaConfig
from miramedia.database import init_engine
from miramedia.smoke.credentials import SmokeAdminCredentials


async def seed_disposable_admin() -> SmokeAdminCredentials:
    """Create a verified superuser so startup bootstrap is skipped.

    ``MIRAMEDIA_CONFIG_DIR`` and ``DATABASE_URL`` must already point at the
    disposable smoke database before this is called.
    """
    config = MiraMediaConfig()
    init_engine(config.database)

    email = f"smoke-{uuid.uuid4().hex}@example.com"
    password = secrets.token_urlsafe(24)

    async with get_async_session_context() as session:
        async with get_user_db_context(session) as user_db:
            async with get_user_manager_context(user_db) as user_manager:
                await user_manager.create(
                    UserCreate(
                        email=email,
                        password=password,
                        is_superuser=True,
                        is_verified=True,
                    )
                )
                await session.commit()

    return SmokeAdminCredentials(email=email, password=password)

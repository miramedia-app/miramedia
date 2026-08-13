"""Auto-remove watched media from custom lists when configured."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.watchlists.repository import WatchlistRepository
from miramedia.watchlists.schemas import WatchlistMediaKind


async def auto_remove_watched_from_lists(
    db: AsyncSession,
    *,
    user_id: UUID,
    media_kind: WatchlistMediaKind,
    media_id: UUID,
) -> int:
    cfg = MiraMediaConfig().watchlists
    if not cfg.auto_remove_watched or not cfg.custom_lists_enabled:
        return 0
    return await WatchlistRepository(db).delete_items_for_media(
        user_id=user_id,
        media_kind=media_kind,
        media_id=media_id,
    )


async def auto_remove_watched_media_ids(
    db: AsyncSession,
    *,
    user_id: UUID,
    media_kind: WatchlistMediaKind,
    media_ids: list[UUID],
) -> int:
    if not media_ids:
        return 0
    cfg = MiraMediaConfig().watchlists
    if not cfg.auto_remove_watched or not cfg.custom_lists_enabled:
        return 0
    return await WatchlistRepository(db).delete_items_for_media_ids(
        user_id=user_id,
        media_kind=media_kind,
        media_ids=media_ids,
    )

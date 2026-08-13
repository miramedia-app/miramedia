"""Cross-user ownership negatives for watchlist repository/service layer."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from miramedia.config import MiraMediaConfig
from miramedia.watchlists.auto_remove import auto_remove_watched_from_lists
from miramedia.watchlists.models import WatchlistItem
from miramedia.watchlists.repository import WatchlistRepository
from miramedia.watchlists.schemas import (
    WatchlistItemCreate,
    WatchlistReorder,
    WatchlistUpdate,
)
from tests.integration.test_watchlists_repository import (
    _create_list,
    _insert_unique_movie_file,
    _seed_user,
    _service,
)

pytestmark = pytest.mark.integration


async def _seed_cross_user_movie_item(
    db,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, object, object]:
    user_a = await _seed_user(db)
    user_b = await _seed_user(db)
    watchlist_id = await _create_list(db, user_id=user_a)
    movie, _ = await _insert_unique_movie_file(db)
    service = await _service(db)
    item, _ = await service.add_item(
        user_id=user_a,
        watchlist_id=watchlist_id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie.id),
    )
    return user_a, user_b, watchlist_id, item, movie


def test_cross_user_list_metadata_operations_are_denied(db, run_async) -> None:
    async def _run() -> None:
        user_a, user_b, watchlist_id, _item, _movie = await _seed_cross_user_movie_item(
            db
        )
        service = await _service(db)

        assert await service.list_watchlists(user_id=user_b) == []
        assert (
            await service.get_watchlist(user_id=user_b, watchlist_id=watchlist_id)
            is None
        )

        updated = await service.update_watchlist(
            user_id=user_b,
            watchlist_id=watchlist_id,
            data=WatchlistUpdate(name="Hijacked"),
        )
        assert updated is None

        owner_detail = await service.get_watchlist(
            user_id=user_a, watchlist_id=watchlist_id
        )
        assert owner_detail is not None
        assert owner_detail.name == "List"

        deleted = await service.delete_watchlist(
            user_id=user_b, watchlist_id=watchlist_id
        )
        assert deleted is False
        assert (
            await service.get_watchlist(user_id=user_a, watchlist_id=watchlist_id)
            is not None
        )

    run_async(_run())


def test_cross_user_item_operations_are_denied(db, run_async) -> None:
    async def _run() -> None:
        user_a, user_b, watchlist_id, item, movie = await _seed_cross_user_movie_item(
            db
        )
        service = await _service(db)
        repo = WatchlistRepository(db)

        with pytest.raises(HTTPException) as exc_info:
            await service.add_item(
                user_id=user_b,
                watchlist_id=watchlist_id,
                data=WatchlistItemCreate(media_kind="movie", media_id=movie.id),
            )
        assert exc_info.value.status_code == 404

        owner_detail = await service.get_watchlist(
            user_id=user_a, watchlist_id=watchlist_id
        )
        assert owner_detail is not None
        assert len(owner_detail.items) == 1

        with pytest.raises(HTTPException) as reorder_exc:
            await service.reorder_items(
                user_id=user_b,
                watchlist_id=watchlist_id,
                data=WatchlistReorder(item_ids=[item.id]),
            )
        assert reorder_exc.value.status_code == 404

        removed = await service.remove_item(
            user_id=user_b,
            watchlist_id=watchlist_id,
            item_id=item.id,
        )
        assert removed is False

        owner_detail_after = await service.get_watchlist(
            user_id=user_a, watchlist_id=watchlist_id
        )
        assert owner_detail_after is not None
        assert len(owner_detail_after.items) == 1
        assert owner_detail_after.items[0].id == item.id

        deleted_for_media = await repo.delete_items_for_media(
            user_id=user_b,
            media_kind="movie",
            media_id=movie.id,
        )
        assert deleted_for_media == 0

        deleted_for_media_ids = await repo.delete_items_for_media_ids(
            user_id=user_b,
            media_kind="movie",
            media_ids=[movie.id],
        )
        assert deleted_for_media_ids == 0

        remaining = (
            (
                await db.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.watchlist_id == watchlist_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(remaining) == 1

    run_async(_run())


def test_auto_remove_watched_from_lists_removes_item_when_flags_on(
    db, run_async
) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie, _ = await _insert_unique_movie_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie.id),
        )

        cfg = MiraMediaConfig().watchlists
        original = (
            cfg.auto_remove_watched,
            cfg.native.enabled,
            cfg.native.custom_lists,
        )
        cfg.auto_remove_watched = True
        cfg.native.enabled = True
        cfg.native.custom_lists = True
        try:
            removed = await auto_remove_watched_from_lists(
                db,
                user_id=user_id,
                media_kind="movie",
                media_id=movie.id,
            )
            assert removed == 1

            remaining = (
                (
                    await db.execute(
                        select(WatchlistItem).where(
                            WatchlistItem.watchlist_id == watchlist_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert remaining == []
        finally:
            (
                cfg.auto_remove_watched,
                cfg.native.enabled,
                cfg.native.custom_lists,
            ) = original

    run_async(_run())

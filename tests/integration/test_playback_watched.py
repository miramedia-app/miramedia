"""Integration tests for per-user watched state."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from miramedia.movies.repository import MovieRepository
from miramedia.playback.repository import PlaybackRepository
from miramedia.playback.schemas import (
    MediaKind,
    PlaybackProgressUpsert,
    WatchStateUpdate,
)
from miramedia.playback.service import PlaybackService
from miramedia.shows.repository import ShowRepository
from tests.integration.builders import insert_movie_file, insert_show_episode_file

pytestmark = pytest.mark.integration


async def _seed_user(db) -> uuid.UUID:
    from miramedia.auth.db import User

    user_id = uuid.uuid4()
    db.add(
        User(
            id=user_id,
            email=f"{user_id.hex}@test.local",
            hashed_password="x",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
    )
    await db.commit()
    return user_id


async def _service(db) -> PlaybackService:
    return PlaybackService(
        PlaybackRepository(db),
        MovieRepository(db),
        ShowRepository(db),
    )


async def _add_watchlist(db, *, user_id: uuid.UUID) -> uuid.UUID:
    from miramedia.watchlists.models import Watchlist

    watchlist_id = uuid.uuid4()
    db.add(
        Watchlist(
            id=watchlist_id,
            user_id=user_id,
            name="Keep Me",
            description=None,
        )
    )
    await db.commit()
    return watchlist_id


def test_clear_viewing_state_does_not_delete_custom_lists(db, run_async) -> None:
    async def _run() -> None:
        from miramedia.playback.models import MediaWatchState, PlaybackProgress
        from miramedia.watchlists.models import Watchlist

        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        watchlist_id = await _add_watchlist(db, user_id=user_id)
        db.add(
            PlaybackProgress(
                user_id=user_id,
                movie_file_id=movie_file.id,
                position_ms=60_000,
                duration_ms=100_000,
                completed=False,
            )
        )
        db.add(
            MediaWatchState(
                user_id=user_id,
                movie_id=movie.id,
                watched=True,
                source="manual",
            )
        )
        await db.commit()

        service = await _service(db)
        await service.delete_all_viewing_state(user_id=user_id)

        progress_rows = (
            (
                await db.execute(
                    select(PlaybackProgress).where(PlaybackProgress.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        watch_rows = (
            (
                await db.execute(
                    select(MediaWatchState).where(MediaWatchState.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        watchlist_rows = (
            (await db.execute(select(Watchlist).where(Watchlist.user_id == user_id)))
            .scalars()
            .all()
        )

        assert progress_rows == []
        assert watch_rows == []
        assert len(watchlist_rows) == 1
        assert watchlist_rows[0].id == watchlist_id

    run_async(_run())


def test_manual_unwatched_over_completed_progress_postgresql(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        service = await _service(db)
        await service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=movie_file.id,
                media_kind=MediaKind.movie,
                position_ms=95_000,
                duration_ms=100_000,
            ),
        )
        await service.set_watched(
            user_id=user_id,
            data=WatchStateUpdate(
                media_kind="movie",
                media_id=movie.id,
                watched=False,
            ),
        )
        state = await service.get_watched(
            user_id=user_id,
            media_kind=MediaKind.movie,
            media_id=movie.id,
        )
        assert state.watched is False
        assert state.source == "manual"

    run_async(_run())


def test_completion_creates_derived_watched_state_postgresql(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        service = await _service(db)
        await service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=movie_file.id,
                media_kind=MediaKind.movie,
                position_ms=95_000,
                duration_ms=100_000,
            ),
        )
        state = await service.get_watched(
            user_id=user_id,
            media_kind=MediaKind.movie,
            media_id=movie.id,
        )
        assert state.watched is True
        assert state.source == "derived"

    run_async(_run())


def test_clear_watched_override_returns_derived_fallback(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        service = await _service(db)
        await service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=movie_file.id,
                media_kind=MediaKind.movie,
                position_ms=95_000,
                duration_ms=100_000,
            ),
        )
        await service.set_watched(
            user_id=user_id,
            data=WatchStateUpdate(
                media_kind="movie",
                media_id=movie.id,
                watched=False,
            ),
        )
        state = await service.clear_watched_override(
            user_id=user_id,
            media_kind=MediaKind.movie,
            media_id=movie.id,
        )
        assert state.watched is True
        assert state.source == "derived"

    run_async(_run())


def test_mark_unwatched_preserves_resume_position(db, run_async) -> None:
    async def _run() -> None:
        from miramedia.playback.models import PlaybackProgress

        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        service = await _service(db)
        await service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=movie_file.id,
                media_kind=MediaKind.movie,
                position_ms=60_000,
                duration_ms=100_000,
            ),
        )
        await service.set_watched(
            user_id=user_id,
            data=WatchStateUpdate(
                media_kind="movie",
                media_id=movie.id,
                watched=False,
            ),
        )
        row = (
            await db.execute(
                select(PlaybackProgress).where(
                    PlaybackProgress.user_id == user_id,
                    PlaybackProgress.movie_file_id == movie_file.id,
                )
            )
        ).scalar_one()
        assert row.position_ms == 60_000

    run_async(_run())


def test_show_batch_excludes_specials_postgresql(db, run_async) -> None:
    async def _run() -> None:
        from miramedia.shows.models import Episode, Season, Show

        user_id = await _seed_user(db)
        show_id = uuid.uuid4()
        season_regular_id = uuid.uuid4()
        season_special_id = uuid.uuid4()
        regular_episode_id = uuid.uuid4()
        special_episode_id = uuid.uuid4()
        db.add_all(
            [
                Show(
                    id=show_id,
                    external_id=f"ext-{show_id.hex[:8]}",
                    metadata_provider="native",
                    name="Batch Show",
                    overview="",
                    year=2026,
                ),
                Season(id=season_special_id, show_id=show_id, number=0),
                Season(id=season_regular_id, show_id=show_id, number=1),
                Episode(
                    id=special_episode_id,
                    season_id=season_special_id,
                    number=1,
                    title="Special",
                ),
                Episode(
                    id=regular_episode_id,
                    season_id=season_regular_id,
                    number=1,
                    title="Pilot",
                ),
            ]
        )
        await db.commit()

        service = await _service(db)
        from miramedia.playback.models import MediaWatchState
        from miramedia.playback.schemas import ShowWatchStateUpdate

        await service.set_show_watched(
            user_id=user_id,
            data=ShowWatchStateUpdate(show_id=show_id, watched=True),
        )

        rows = (
            (
                await db.execute(
                    select(MediaWatchState).where(MediaWatchState.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        episode_ids = {row.episode_id for row in rows}
        assert regular_episode_id in episode_ids
        assert special_episode_id not in episode_ids

    run_async(_run())


def test_auto_remove_watched_media_ids_batch_removes_only_owner_items(
    db, run_async
) -> None:
    async def _run() -> None:
        from miramedia.config import MiraMediaConfig
        from miramedia.watchlists.auto_remove import auto_remove_watched_media_ids
        from miramedia.watchlists.models import WatchlistItem

        user_a = await _seed_user(db)
        user_b = await _seed_user(db)
        _show, episode_file = await insert_show_episode_file(db)
        episode_id = episode_file.episode_id
        watchlist_a = await _add_watchlist(db, user_id=user_a)
        watchlist_b = await _add_watchlist(db, user_id=user_b)
        item_a_id = uuid.uuid4()
        item_b_id = uuid.uuid4()
        db.add_all(
            [
                WatchlistItem(
                    id=item_a_id,
                    watchlist_id=watchlist_a,
                    position=0,
                    episode_id=episode_id,
                ),
                WatchlistItem(
                    id=item_b_id,
                    watchlist_id=watchlist_b,
                    position=0,
                    episode_id=episode_id,
                ),
            ]
        )
        await db.commit()

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
            removed = await auto_remove_watched_media_ids(
                db,
                user_id=user_a,
                media_kind="episode",
                media_ids=[episode_id],
            )
            assert removed == 1

            item_rows = (await db.execute(select(WatchlistItem))).scalars().all()
            item_ids = {row.id for row in item_rows}
            assert item_a_id not in item_ids
            assert item_b_id in item_ids

            cfg.auto_remove_watched = False
            removed_off = await auto_remove_watched_media_ids(
                db,
                user_id=user_b,
                media_kind="episode",
                media_ids=[episode_id],
            )
            assert removed_off == 0

            item_rows_after = (await db.execute(select(WatchlistItem))).scalars().all()
            item_ids_after = {row.id for row in item_rows_after}
            assert item_b_id in item_ids_after
        finally:
            (
                cfg.auto_remove_watched,
                cfg.native.enabled,
                cfg.native.custom_lists,
            ) = original

    run_async(_run())

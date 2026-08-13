"""PostgreSQL integration tests for private watchlists and watch state."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


async def _add_and_commit(db, obj) -> None:
    db.add(obj)
    await db.commit()


async def _add_watchlist(
    db, *, user_id: uuid.UUID, name: str = "Favorites"
) -> uuid.UUID:
    from miramedia.watchlists.models import Watchlist

    watchlist_id = uuid.uuid4()
    db.add(
        Watchlist(
            id=watchlist_id,
            user_id=user_id,
            name=name,
            description=None,
        )
    )
    await db.commit()
    return watchlist_id


def test_watchlist_rows_are_owner_scoped_and_cascade_on_user_delete(
    db, run_async
) -> None:
    async def _run() -> None:
        from miramedia.auth.db import User
        from miramedia.watchlists.models import Watchlist, WatchlistItem

        owner_id = await _seed_user(db)
        other_id = await _seed_user(db)
        movie, _movie_file = await insert_movie_file(db)

        watchlist_id = await _add_watchlist(db, user_id=owner_id, name="Mine")
        db.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                position=0,
                movie_id=movie.id,
            )
        )
        await db.commit()

        await db.delete(await db.get(User, owner_id))
        await db.commit()

        remaining_lists = (
            (await db.execute(select(Watchlist).where(Watchlist.user_id == owner_id)))
            .scalars()
            .all()
        )
        assert remaining_lists == []

        other_lists = (
            (await db.execute(select(Watchlist).where(Watchlist.user_id == other_id)))
            .scalars()
            .all()
        )
        assert other_lists == []

    run_async(_run())


def test_watch_state_requires_exactly_one_logical_media_fk(db, run_async) -> None:
    async def _run() -> None:
        from miramedia.playback.models import MediaWatchState, WatchStateSource

        user_id = await _seed_user(db)
        movie, _movie_file = await insert_movie_file(db)
        _show, episode_file = await insert_show_episode_file(db)
        movie_id = movie.id
        episode_id = episode_file.episode_id

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                MediaWatchState(
                    user_id=user_id,
                    movie_id=None,
                    episode_id=None,
                    watched=True,
                    source=WatchStateSource.manual,
                ),
            )
        await db.rollback()

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                MediaWatchState(
                    user_id=user_id,
                    movie_id=movie_id,
                    episode_id=episode_id,
                    watched=True,
                    source=WatchStateSource.manual,
                ),
            )
        await db.rollback()

        db.add(
            MediaWatchState(
                user_id=user_id,
                movie_id=movie_id,
                episode_id=None,
                watched=True,
                source=WatchStateSource.manual,
            )
        )
        await db.commit()

    run_async(_run())


def test_watchlist_item_requires_exactly_one_movie_show_or_episode_fk(
    db, run_async
) -> None:
    async def _run() -> None:
        from miramedia.watchlists.models import WatchlistItem

        user_id = await _seed_user(db)
        watchlist_id = await _add_watchlist(db, user_id=user_id)
        movie, _movie_file = await insert_movie_file(db)
        show, _episode_file = await insert_show_episode_file(db)
        movie_id = movie.id
        show_id = show.id
        episode_id = _episode_file.episode_id

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                WatchlistItem(
                    watchlist_id=watchlist_id,
                    position=0,
                ),
            )
        await db.rollback()

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                WatchlistItem(
                    watchlist_id=watchlist_id,
                    position=0,
                    movie_id=movie_id,
                    show_id=show_id,
                ),
            )
        await db.rollback()

        db.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                position=0,
                episode_id=episode_id,
            )
        )
        await db.commit()

    run_async(_run())


def test_watchlist_rejects_duplicate_owner_name_case_insensitively(
    db, run_async
) -> None:
    async def _run() -> None:
        from miramedia.watchlists.models import Watchlist

        user_id = await _seed_user(db)
        await _add_watchlist(db, user_id=user_id, name="Favorites")

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                Watchlist(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    name="favorites",
                    description=None,
                ),
            )
        await db.rollback()

    run_async(_run())


async def _insert_second_movie(db):
    from miramedia.file_status import ImportOutcome
    from miramedia.movies.models import Movie, MovieFile
    from miramedia.torrents.schemas import Quality

    movie_id = uuid.uuid4()
    file_id = uuid.uuid4()
    movie = Movie(
        id=movie_id,
        external_id=f"ext-movie-{movie_id.hex}",
        metadata_provider="native",
        name="Other Integration Movie",
        overview="",
        year=2026,
    )
    movie_file = MovieFile(
        id=file_id,
        movie_id=movie_id,
        quality=Quality.hd,
        import_status=ImportOutcome.imported,
    )
    db.add_all([movie, movie_file])
    await db.commit()
    return movie, movie_file


def test_watchlist_rejects_duplicate_item_and_position(db, run_async) -> None:
    async def _run() -> None:
        from miramedia.watchlists.models import WatchlistItem

        user_id = await _seed_user(db)
        watchlist_id = await _add_watchlist(db, user_id=user_id)
        movie, _movie_file = await insert_movie_file(db)
        other_movie, _other_file = await _insert_second_movie(db)
        movie_id = movie.id
        other_movie_id = other_movie.id

        db.add(
            WatchlistItem(
                watchlist_id=watchlist_id,
                position=0,
                movie_id=movie_id,
            )
        )
        await db.commit()

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                WatchlistItem(
                    watchlist_id=watchlist_id,
                    position=0,
                    movie_id=other_movie_id,
                ),
            )
        await db.rollback()

        with pytest.raises(IntegrityError):
            await _add_and_commit(
                db,
                WatchlistItem(
                    watchlist_id=watchlist_id,
                    position=1,
                    movie_id=movie_id,
                ),
            )
        await db.rollback()

    run_async(_run())

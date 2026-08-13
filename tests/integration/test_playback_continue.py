"""Plan 288: continue-watching repository on real Postgres."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from miramedia.playback.repository import PlaybackRepository
from miramedia.playback.schemas import MediaKind
from tests.integration.builders import insert_movie_file, insert_show_episode_file

pytestmark = pytest.mark.integration


async def _insert_movie_file(db):
    """Like builders.insert_movie_file but with a unique external_id."""
    from miramedia.file_status import ImportOutcome
    from miramedia.movies.models import Movie, MovieFile
    from miramedia.torrents.schemas import Quality

    movie_id = uuid.uuid4()
    file_id = uuid.uuid4()
    movie = Movie(
        id=movie_id,
        external_id=f"ext-movie-{movie_id.hex}",
        metadata_provider="native",
        name="Integration Movie",
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


async def _add_progress(
    db,
    *,
    user_id: uuid.UUID,
    movie_file_id: uuid.UUID | None = None,
    episode_file_id: uuid.UUID | None = None,
    position_ms: int = 60_000,
    duration_ms: int = 100_000,
    completed: bool = False,
    updated_at: datetime | None = None,
) -> None:
    from miramedia.playback.models import PlaybackProgress

    db.add(
        PlaybackProgress(
            user_id=user_id,
            movie_file_id=movie_file_id,
            episode_file_id=episode_file_id,
            position_ms=position_ms,
            duration_ms=duration_ms,
            completed=completed,
            updated_at=updated_at or datetime.now(UTC),
        )
    )
    await db.commit()


def test_orders_by_updated_at_desc(db, run_async) -> None:
    async def _run_test() -> None:
        user_id = await _seed_user(db)
        _movie_a, movie_file_a = await _insert_movie_file(db)
        _movie_b, movie_file_b = await _insert_movie_file(db)
        older = datetime.now(UTC) - timedelta(minutes=10)
        newer = datetime.now(UTC) - timedelta(minutes=1)
        await _add_progress(
            db,
            user_id=user_id,
            movie_file_id=movie_file_a.id,
            updated_at=older,
        )
        await _add_progress(
            db,
            user_id=user_id,
            movie_file_id=movie_file_b.id,
            updated_at=newer,
        )

        items = await PlaybackRepository(db).list_continue(user_id=user_id, limit=10)

        assert [item.file_id for item in items] == [movie_file_b.id, movie_file_a.id]

    run_async(_run_test())


def test_excludes_completed_rows(db, run_async) -> None:
    async def _run_test() -> None:
        user_id = await _seed_user(db)
        _movie_done, movie_file_done = await _insert_movie_file(db)
        _movie_active, movie_file_active = await _insert_movie_file(db)
        await _add_progress(
            db,
            user_id=user_id,
            movie_file_id=movie_file_done.id,
            completed=True,
        )
        await _add_progress(
            db,
            user_id=user_id,
            movie_file_id=movie_file_active.id,
            completed=False,
        )

        items = await PlaybackRepository(db).list_continue(user_id=user_id, limit=10)

        assert len(items) == 1
        assert items[0].file_id == movie_file_active.id

    run_async(_run_test())


def test_limit_counts_only_renderable_rows(db, run_async) -> None:
    async def _run_test() -> None:
        user_id = await _seed_user(db)
        now = datetime.now(UTC)
        file_ids: list[uuid.UUID] = []
        for offset_minutes in (30, 20, 10):
            _movie, movie_file = await _insert_movie_file(db)
            file_ids.append(movie_file.id)
            await _add_progress(
                db,
                user_id=user_id,
                movie_file_id=movie_file.id,
                updated_at=now - timedelta(minutes=offset_minutes),
            )

        items = await PlaybackRepository(db).list_continue(user_id=user_id, limit=2)

        assert len(items) == 2
        assert [item.file_id for item in items] == [file_ids[2], file_ids[1]]

    run_async(_run_test())


def test_movie_label_shape(db, run_async) -> None:
    async def _run_test() -> None:
        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        await _add_progress(db, user_id=user_id, movie_file_id=movie_file.id)

        items = await PlaybackRepository(db).list_continue(user_id=user_id, limit=10)

        assert len(items) == 1
        item = items[0]
        assert item.title == "Integration Movie"
        assert item.year == 2026
        assert item.media_kind == MediaKind.movie
        assert item.media_id == movie.id
        assert item.poster_media_id == movie.id
        assert item.show_id is None

    run_async(_run_test())


def test_episode_label_shape(db, run_async) -> None:
    async def _run_test() -> None:
        user_id = await _seed_user(db)
        show, episode_file = await insert_show_episode_file(db)
        await _add_progress(db, user_id=user_id, episode_file_id=episode_file.id)

        items = await PlaybackRepository(db).list_continue(user_id=user_id, limit=10)

        assert len(items) == 1
        item = items[0]
        assert item.title == "Integration Show"
        assert item.season_number == 1
        assert item.episode_number == 1
        assert item.media_kind == MediaKind.episode
        assert item.show_id == show.id
        assert item.poster_media_id == show.id

    run_async(_run_test())


def test_scoped_to_user(db, run_async) -> None:
    async def _run_test() -> None:
        user_a = await _seed_user(db)
        user_b = await _seed_user(db)
        _movie_a, movie_file_a = await _insert_movie_file(db)
        _movie_b, movie_file_b = await _insert_movie_file(db)
        await _add_progress(db, user_id=user_a, movie_file_id=movie_file_a.id)
        await _add_progress(db, user_id=user_b, movie_file_id=movie_file_b.id)

        repo = PlaybackRepository(db)
        items_a = await repo.list_continue(user_id=user_a, limit=10)
        items_b = await repo.list_continue(user_id=user_b, limit=10)

        assert len(items_a) == 1
        assert items_a[0].file_id == movie_file_a.id
        assert len(items_b) == 1
        assert items_b[0].file_id == movie_file_b.id

    run_async(_run_test())

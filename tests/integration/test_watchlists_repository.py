"""PostgreSQL integration tests for watchlist item resolution."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from miramedia.file_status import ImportOutcome
from miramedia.movies.repository import MovieRepository
from miramedia.playback.models import (
    MediaWatchState,
    PlaybackProgress,
    WatchStateSource,
)
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.repository import ShowRepository
from miramedia.watchlists.models import WatchlistItem
from miramedia.watchlists.repository import WatchlistRepository
from miramedia.watchlists.schemas import (
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistReorder,
)
from miramedia.watchlists.service import WatchlistService
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


async def _service(db) -> WatchlistService:
    return WatchlistService(
        WatchlistRepository(db),
        MovieRepository(db),
        ShowRepository(db),
    )


async def _create_list(db, *, user_id: uuid.UUID, name: str = "List") -> uuid.UUID:
    service = await _service(db)
    detail = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name=name),
    )
    return detail.id


async def _insert_unique_movie_file(db):
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


async def _insert_unique_show_episode_file(
    db,
    *,
    import_status: ImportOutcome = ImportOutcome.imported,
) -> tuple[Show, EpisodeFile]:
    from miramedia.torrents.schemas import Quality

    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    file_id = uuid.uuid4()
    show = Show(
        id=show_id,
        external_id=f"ext-show-{show_id.hex}",
        metadata_provider="native",
        name="Integration Show",
        overview="",
        year=2026,
    )
    season = Season(id=season_id, show_id=show_id, number=1)
    episode = Episode(
        id=episode_id,
        season_id=season_id,
        number=1,
        title="Pilot",
        overview=None,
    )
    episode_file = EpisodeFile(
        id=file_id,
        episode_id=episode_id,
        quality=Quality.hd,
        import_status=import_status,
    )
    db.add_all([show, season, episode, episode_file])
    await db.commit()
    return show, episode_file


def test_movie_item_resolves_title_poster_and_watched(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie, movie_file = await insert_movie_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie.id),
        )
        db.add(
            MediaWatchState(
                user_id=user_id,
                movie_id=movie.id,
                watched=True,
                source=WatchStateSource.manual,
            )
        )
        await db.commit()

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)

        assert detail is not None
        item = detail.items[0]
        assert item.media_kind == "movie"
        assert item.title == movie.name
        assert item.poster_media_id == movie.id
        assert item.watched is True
        assert item.file_id == movie_file.id
        assert item.year == movie.year

    run_async(_run())


def test_episode_item_resolves_playback_progress(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        show, episode_file = await insert_show_episode_file(db)
        episode_id = episode_file.episode_id
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="episode", media_id=episode_id),
        )
        db.add(
            PlaybackProgress(
                user_id=user_id,
                episode_file_id=episode_file.id,
                position_ms=30_000,
                duration_ms=100_000,
                completed=False,
            )
        )
        await db.commit()

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)

        assert detail is not None
        item = detail.items[0]
        assert item.media_kind == "episode"
        assert item.show_id == show.id
        assert item.file_id == episode_file.id
        assert item.position_ms == 30_000
        assert item.watched is False

    run_async(_run())


def test_show_item_resolves_next_episode_for_unstarted_show(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        show, episode_file = await insert_show_episode_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="show", media_id=show.id),
        )

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)

        assert detail is not None
        item = detail.items[0]
        assert item.media_kind == "show"
        assert item.next_episode is not None
        assert item.next_episode.media_id == episode_file.episode_id
        assert item.next_episode.file_id == episode_file.id
        assert item.show_status is None

    run_async(_run())


def test_show_item_without_download_reports_no_download_status(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        show, _episode_file = await insert_show_episode_file(
            db, import_status=ImportOutcome.pending
        )
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="show", media_id=show.id),
        )

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)

        assert detail is not None
        item = detail.items[0]
        assert item.next_episode is None
        assert item.show_status == "no_downloaded_episode_available"

    run_async(_run())


def test_show_item_all_watched_reports_completion_status(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        show, episode_file = await insert_show_episode_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="show", media_id=show.id),
        )
        db.add(
            MediaWatchState(
                user_id=user_id,
                episode_id=episode_file.episode_id,
                watched=True,
                source=WatchStateSource.manual,
            )
        )
        await db.commit()

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)

        assert detail is not None
        item = detail.items[0]
        assert item.next_episode is None
        assert item.show_status == "all_available_episodes_watched"

    run_async(_run())


def test_items_return_in_manual_position_order(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie_a, _ = await _insert_unique_movie_file(db)
        movie_b, _ = await _insert_unique_movie_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        second, _ = await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_b.id),
        )
        first, _ = await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_a.id),
        )
        await service.reorder_items(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistReorder(item_ids=[first.id, second.id]),
        )

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)

        assert detail is not None
        assert [item.media_id for item in detail.items] == [movie_a.id, movie_b.id]
        assert [item.position for item in detail.items] == [0, 1]

    run_async(_run())


def test_reorder_succeeds_after_removal_leaves_position_gap(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie_a, _ = await _insert_unique_movie_file(db)
        movie_b, _ = await _insert_unique_movie_file(db)
        movie_c, _ = await _insert_unique_movie_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        first, _ = await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_a.id),
        )
        second, _ = await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_b.id),
        )
        third, _ = await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_c.id),
        )
        # Removing the first item leaves surviving positions {1, 2} — both
        # >= len(remaining) with the old offset = len(item_ids) = 2.
        removed = await service.remove_item(
            user_id=user_id, watchlist_id=watchlist_id, item_id=first.id
        )
        assert removed is True

        detail = await service.reorder_items(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistReorder(item_ids=[third.id, second.id]),
        )

        assert [item.id for item in detail.items] == [third.id, second.id]
        assert [item.position for item in detail.items] == [0, 1]

    run_async(_run())


def test_remove_item_miss_leaves_session_clean(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)

        removed = await service.remove_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            item_id=uuid.uuid4(),  # no such item -> rowcount 0
        )
        assert removed is False
        assert db.in_transaction() is False
        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)
        assert detail is not None
        assert detail.items == []

    run_async(_run())


def test_create_duplicate_name_race_raises_conflict(db, run_async) -> None:
    async def _run() -> None:
        from miramedia.exceptions import ConflictError

        user_id = await _seed_user(db)
        repo = WatchlistRepository(db)
        await repo.create(user_id=user_id, name="Favorites", description=None)
        with pytest.raises(ConflictError):
            await repo.create(user_id=user_id, name="FAVORITES", description=None)
        assert await repo.count_lists(user_id=user_id) == 1

    run_async(_run())


def test_mixed_kind_list_resolves_all_items_batched(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)

        movie_manual, movie_manual_file = await _insert_unique_movie_file(db)
        movie_progress, movie_progress_file = await _insert_unique_movie_file(db)
        show_with_ep, episode_file = await _insert_unique_show_episode_file(db)
        _, episode_progress_file = await _insert_unique_show_episode_file(db)
        episode_id_with_progress = episode_progress_file.episode_id
        show_no_download, _ = await _insert_unique_show_episode_file(
            db, import_status=ImportOutcome.pending
        )

        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_manual.id),
        )
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie_progress.id),
        )
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(
                media_kind="episode", media_id=episode_id_with_progress
            ),
        )
        _, untouched_episode_file = await _insert_unique_show_episode_file(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(
                media_kind="episode", media_id=untouched_episode_file.episode_id
            ),
        )
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="show", media_id=show_with_ep.id),
        )
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="show", media_id=show_no_download.id),
        )

        db.add(
            MediaWatchState(
                user_id=user_id,
                movie_id=movie_manual.id,
                watched=True,
                source=WatchStateSource.manual,
            )
        )
        db.add(
            PlaybackProgress(
                user_id=user_id,
                movie_file_id=movie_progress_file.id,
                position_ms=50_000,
                duration_ms=100_000,
                completed=True,
            )
        )
        db.add(
            PlaybackProgress(
                user_id=user_id,
                episode_file_id=episode_progress_file.id,
                position_ms=12_000,
                duration_ms=100_000,
                completed=False,
            )
        )
        await db.commit()

        detail = await WatchlistRepository(db).get_detail(
            user_id=user_id, watchlist_id=watchlist_id
        )

        assert detail is not None
        assert [i.position for i in detail.items] == list(range(6))

        by_media = {item.media_id: item for item in detail.items}

        manual_item = by_media[movie_manual.id]
        assert manual_item.media_kind == "movie"
        assert manual_item.title == movie_manual.name
        assert manual_item.watched is True
        assert manual_item.file_id == movie_manual_file.id

        progress_movie_item = by_media[movie_progress.id]
        assert progress_movie_item.media_kind == "movie"
        assert progress_movie_item.watched is True
        assert progress_movie_item.file_id == movie_progress_file.id

        episode_progress_item = by_media[episode_id_with_progress]
        assert episode_progress_item.media_kind == "episode"
        assert episode_progress_item.file_id == episode_progress_file.id
        assert episode_progress_item.position_ms == 12_000
        assert episode_progress_item.watched is False

        untouched_item = by_media[untouched_episode_file.episode_id]
        assert untouched_item.media_kind == "episode"
        assert untouched_item.file_id == untouched_episode_file.id
        assert untouched_item.position_ms == 0
        assert untouched_item.watched is False

        show_item = by_media[show_with_ep.id]
        assert show_item.media_kind == "show"
        assert show_item.next_episode is not None
        assert show_item.next_episode.media_id == episode_file.episode_id
        assert show_item.next_episode.file_id == episode_file.id
        assert show_item.show_status is None

        no_download_item = by_media[show_no_download.id]
        assert no_download_item.media_kind == "show"
        assert no_download_item.next_episode is None
        assert no_download_item.show_status == "no_downloaded_episode_available"

    run_async(_run())


def test_media_delete_cascades_watchlist_items(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        movie, _movie_file = await insert_movie_file(db)
        watchlist_id = await _create_list(db, user_id=user_id)
        service = await _service(db)
        await service.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistItemCreate(media_kind="movie", media_id=movie.id),
        )
        await db.delete(movie)
        await db.commit()

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

        detail = await service.get_watchlist(user_id=user_id, watchlist_id=watchlist_id)
        assert detail is not None
        assert detail.items == []

    run_async(_run())

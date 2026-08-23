"""PostgreSQL integration tests for show and movie repository mutations."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from miramedia.exceptions import ConflictError, NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.movies.models import Movie, MovieFile
from miramedia.movies.repository import MovieRepository
from miramedia.movies.schemas import Movie as MovieSchema
from miramedia.movies.schemas import MovieFile as MovieFileSchema
from miramedia.movies.schemas import MovieId
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import Episode as EpisodeSchema
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.shows.schemas import (
    EpisodeId,
    EpisodeNumber,
    SeasonId,
    SeasonNumber,
    ShowId,
)
from miramedia.shows.schemas import Season as SeasonSchema
from miramedia.shows.schemas import Show as ShowSchema
from miramedia.torrents.schemas import Quality
from tests.integration.builders import insert_movie_file, insert_show_episode_file

pytestmark = pytest.mark.integration


def _unique_external(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _make_show_schema(
    *,
    external_id: str | None = None,
    name: str = "Repo Show",
) -> ShowSchema:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episode_id = EpisodeId(uuid.uuid4())
    ext = external_id or _unique_external("ext-show")
    return ShowSchema(
        id=show_id,
        external_id=ext,
        metadata_provider="native",
        name=name,
        overview="overview",
        year=2026,
        seasons=[
            SeasonSchema(
                id=season_id,
                show_id=show_id,
                number=SeasonNumber(1),
                episodes=[
                    EpisodeSchema(
                        id=episode_id,
                        number=EpisodeNumber(1),
                        title="Pilot",
                    )
                ],
            )
        ],
    )


def _make_movie_schema(
    *,
    external_id: str | None = None,
    name: str = "Repo Movie",
) -> MovieSchema:
    return MovieSchema(
        id=MovieId(uuid.uuid4()),
        external_id=external_id or _unique_external("ext-movie"),
        metadata_provider="native",
        name=name,
        overview="overview",
        year=2026,
    )


def test_movie_crud_with_file(db, run_async) -> None:
    async def _run() -> None:
        repo = MovieRepository(db)
        movie = _make_movie_schema()

        created = await repo.save_movie(movie)
        assert created.name == movie.name
        assert created.id == movie.id

        fetched = await repo.get_movie_by_id(created.id)
        assert fetched.name == movie.name

        updated, changed = await repo.update_movie_attributes(
            created.id, name="Updated Movie"
        )
        assert changed is True
        assert updated.name == "Updated Movie"

        file_schema = MovieFileSchema(
            movie_id=created.id,
            quality=Quality.hd,
            variant="main",
        )
        added_file = await repo.add_movie_file(file_schema)
        assert added_file.movie_id == created.id

        await repo.update_movie_file_import_status(
            file_id=added_file.id,
            status=ImportOutcome.imported,
        )
        await db.commit()

        files = await repo.get_movie_files_by_movie_id(created.id)
        assert len(files) == 1
        assert files[0].import_status == ImportOutcome.imported

        await repo.delete_movie_file(added_file.id)
        files = await repo.get_movie_files_by_movie_id(created.id)
        assert files == []

        await repo.delete_movie(created.id)
        with pytest.raises(NotFoundError):
            await repo.get_movie_by_id(created.id)

    run_async(_run())


def test_show_crud_with_tree_and_file(db, run_async) -> None:
    async def _run() -> None:
        repo = ShowRepository(db)
        show = _make_show_schema()

        created = await repo.save_show(show)
        assert created.name == show.name
        assert len(created.seasons) == 1
        assert len(created.seasons[0].episodes) == 1

        fetched = await repo.get_show_by_id(created.id)
        assert fetched.name == show.name

        updated, changed = await repo.update_show_attributes(
            created.id, name="Updated Show"
        )
        assert changed is True
        assert updated.name == "Updated Show"

        episode_id = created.seasons[0].episodes[0].id
        file_schema = EpisodeFileSchema(
            episode_id=episode_id,
            quality=Quality.hd,
            torrent_id=None,
            variant="main",
        )
        added_file = await repo.add_episode_file(file_schema)
        assert added_file.episode_id == episode_id

        await repo.update_episode_file_import_status(
            file_id=added_file.id,
            status=ImportOutcome.imported,
        )
        await db.commit()

        files = await repo.get_episode_files_by_episode_id(episode_id)
        assert len(files) == 1
        assert files[0].import_status == ImportOutcome.imported

        await repo.delete_episode_file(added_file.id)
        files = await repo.get_episode_files_by_episode_id(episode_id)
        assert files == []

        await repo.delete_show(created.id)
        with pytest.raises(NotFoundError):
            await repo.get_show_by_id(created.id)

    run_async(_run())


def test_delete_show_cascades_season_episode_files(db, run_async) -> None:
    async def _run() -> None:
        show, episode_file = await insert_show_episode_file(db)
        season_id = (
            await db.execute(select(Season.id).where(Season.show_id == show.id))
        ).scalar_one()
        episode_id = episode_file.episode_id

        await ShowRepository(db).delete_show(ShowId(show.id))

        assert (
            await db.execute(select(Show).where(Show.id == show.id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(Season).where(Season.id == season_id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(Episode).where(Episode.id == episode_id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(
                select(EpisodeFile).where(EpisodeFile.id == episode_file.id)
            )
        ).scalar_one_or_none() is None

    run_async(_run())


def test_delete_movie_cascades_files(db, run_async) -> None:
    async def _run() -> None:
        movie, movie_file = await insert_movie_file(db)

        await MovieRepository(db).delete_movie(MovieId(movie.id))

        assert (
            await db.execute(select(Movie).where(Movie.id == movie.id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(MovieFile).where(MovieFile.id == movie_file.id))
        ).scalar_one_or_none() is None

    run_async(_run())


def test_save_show_duplicate_external_id_raises_conflict(db, run_async) -> None:
    async def _run() -> None:
        repo = ShowRepository(db)
        external_id = _unique_external("dup-show")
        first = await repo.save_show(_make_show_schema(external_id=external_id))
        duplicate = _make_show_schema(external_id=external_id)

        with pytest.raises(ConflictError):
            await repo.save_show(duplicate)

        recovered = await repo.save_show(_make_show_schema())
        assert recovered.id != first.id
        still = await repo.get_show_by_id(first.id)
        assert still.name == first.name

    run_async(_run())


def test_save_movie_duplicate_external_id_raises_conflict(db, run_async) -> None:
    async def _run() -> None:
        repo = MovieRepository(db)
        external_id = _unique_external("dup-movie")
        first = await repo.save_movie(_make_movie_schema(external_id=external_id))
        duplicate = _make_movie_schema(external_id=external_id)

        with pytest.raises(ConflictError):
            await repo.save_movie(duplicate)

        recovered = await repo.save_movie(_make_movie_schema())
        assert recovered.id != first.id
        still = await repo.get_movie_by_id(first.id)
        assert still.name == first.name

    run_async(_run())


def test_add_movie_file_duplicate_naming_tuple_raises_integrity_error(
    db, run_async
) -> None:
    async def _run() -> None:
        repo = MovieRepository(db)
        movie = await repo.save_movie(_make_movie_schema())
        base = MovieFileSchema(
            movie_id=movie.id,
            quality=Quality.hd,
            codec="x264",
            variant="main",
            extra="",
        )
        await repo.add_movie_file(base)

        duplicate = MovieFileSchema(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.hd,
            codec="x264",
            variant="main",
            extra="",
        )
        with pytest.raises(IntegrityError):
            await repo.add_movie_file(duplicate)

        distinct = await repo.add_movie_file(
            MovieFileSchema(
                movie_id=movie.id,
                quality=Quality.uhd,
                codec="x265",
                variant="alt",
                extra="",
            )
        )
        assert distinct.quality == Quality.uhd

    run_async(_run())


def test_add_episode_file_duplicate_naming_tuple_raises_integrity_error(
    db, run_async
) -> None:
    async def _run() -> None:
        repo = ShowRepository(db)
        show = await repo.save_show(_make_show_schema())
        episode_id = show.seasons[0].episodes[0].id
        base = EpisodeFileSchema(
            episode_id=episode_id,
            quality=Quality.hd,
            torrent_id=None,
            codec="x264",
            variant="main",
            extra="",
        )
        await repo.add_episode_file(base)

        duplicate = EpisodeFileSchema(
            id=uuid.uuid4(),
            episode_id=episode_id,
            quality=Quality.hd,
            torrent_id=None,
            codec="x264",
            variant="main",
            extra="",
        )
        with pytest.raises(IntegrityError):
            await repo.add_episode_file(duplicate)

        distinct = await repo.add_episode_file(
            EpisodeFileSchema(
                episode_id=episode_id,
                quality=Quality.uhd,
                torrent_id=None,
                codec="x265",
                variant="alt",
                extra="",
            )
        )
        assert distinct.quality == Quality.uhd

    run_async(_run())


def test_movie_file_import_status_visible_in_fresh_session(
    db, make_session, run_async
) -> None:
    async def _run() -> None:
        repo = MovieRepository(db)
        movie, movie_file = await insert_movie_file(db)

        await repo.update_movie_file_import_status(
            file_id=movie_file.id,
            status=ImportOutcome.failed_io,
            error="disk error",
        )
        await db.commit()

        fresh = make_session()
        loaded = await MovieRepository(fresh).get_movie_file_by_id(movie_file.id)
        assert loaded is not None
        assert loaded.import_status == ImportOutcome.failed_io
        assert loaded.import_error == "disk error"
        await fresh.close()

        still = await repo.get_movie_by_id(MovieId(movie.id))
        assert still.name == movie.name

    run_async(_run())


def test_episode_file_import_status_visible_in_fresh_session(
    db, make_session, run_async
) -> None:
    async def _run() -> None:
        repo = ShowRepository(db)
        show, episode_file = await insert_show_episode_file(db)

        await repo.update_episode_file_import_status(
            file_id=episode_file.id,
            status=ImportOutcome.failed_no_match,
            error="no match",
        )
        await db.commit()

        fresh = make_session()
        loaded = await ShowRepository(fresh).get_episode_file_by_id(episode_file.id)
        assert loaded is not None
        assert loaded.import_status == ImportOutcome.failed_no_match
        assert loaded.import_error == "no match"
        await fresh.close()

        still = await repo.get_show_by_id(ShowId(show.id))
        assert still.name == show.name

    run_async(_run())


def test_failed_constraint_rollback_leaves_session_recoverable(db, run_async) -> None:
    async def _run() -> None:
        show_repo = ShowRepository(db)
        movie_repo = MovieRepository(db)
        shared_external = _unique_external("shared-dup")

        await show_repo.save_show(_make_show_schema(external_id=shared_external))
        with pytest.raises(ConflictError):
            await show_repo.save_show(_make_show_schema(external_id=shared_external))

        show_after = await show_repo.save_show(_make_show_schema())
        assert show_after.name == "Repo Show"

        await movie_repo.save_movie(_make_movie_schema(external_id=shared_external))
        with pytest.raises(ConflictError):
            await movie_repo.save_movie(_make_movie_schema(external_id=shared_external))

        movie_after = await movie_repo.save_movie(_make_movie_schema())
        assert movie_after.name == "Repo Movie"
        loaded = await movie_repo.get_movie_by_id(movie_after.id)
        assert loaded.name == "Repo Movie"

    run_async(_run())

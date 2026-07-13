"""Plan 082 scalar show/movie lookups on real AsyncSession."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import MissingGreenlet

from miramedia.movies.repository import MovieRepository
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import ShowId
from tests.integration.builders import insert_movie_file, insert_show_episode_file

pytestmark = pytest.mark.integration


def test_get_shows_by_ids_returns_empty_seasons_without_lazy_load(
    db, run_async
) -> None:
    async def _run_test() -> None:
        show, _episode_file = await insert_show_episode_file(
            db,
            import_error="sha1 mismatch (expected a…, got b…)",
        )
        repo = ShowRepository(db)
        loaded = await repo.get_shows_by_ids([ShowId(show.id)])

        assert len(loaded) == 1
        row = loaded[ShowId(show.id)]
        assert row.name == "Integration Show"
        assert row.seasons == []
        # Touching seasons must not trigger async lazy-load.
        assert row.seasons == []

    run_async(_run_test())


def test_get_shows_by_ids_never_raises_missing_greenlet(db, run_async) -> None:
    async def _run_test() -> None:
        show, _episode_file = await insert_show_episode_file(db)
        repo = ShowRepository(db)
        loaded = await repo.get_shows_by_ids([ShowId(show.id)])
        row = loaded[ShowId(show.id)]
        try:
            _ = row.seasons
            _ = row.name
            _ = row.library
        except MissingGreenlet as exc:
            pytest.fail(f"get_shows_by_ids mapping triggered lazy load: {exc}")

    run_async(_run_test())


def test_get_movies_by_ids_scalar_mapping(db, run_async) -> None:
    async def _run_test() -> None:
        movie, _movie_file = await insert_movie_file(db)
        repo = MovieRepository(db)
        from miramedia.movies.schemas import MovieId

        loaded = await repo.get_movies_by_ids([MovieId(movie.id)])
        row = loaded[MovieId(movie.id)]
        assert row.name == "Integration Movie"
        assert row.library == ""
        assert row.id == movie.id

    run_async(_run_test())

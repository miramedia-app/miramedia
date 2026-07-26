"""DB-free compile tests for shared show/movie list filter and sort builders."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from miramedia.media_filters import apply_list_filters, apply_sort
from miramedia.movies.models import Movie, MovieFile  # noqa: F401
from miramedia.shows.models import (
    Episode,  # noqa: F401
    EpisodeFile,  # noqa: F401
    Season,  # noqa: F401
    Show,
)
from miramedia.torrents.models import Torrent  # noqa: F401


def _compile(stmt, *, literal_binds: bool = False) -> str:
    compile_kwargs = {"literal_binds": True} if literal_binds else {}
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs=compile_kwargs)
    )


def _apply_filters(**kwargs: object) -> str:
    stmt = apply_list_filters(
        select(Show),
        name_col=Show.name,
        library_col=Show.library,
        genres_col=Show.genres,
        year_col=Show.year,
        **kwargs,  # type: ignore[arg-type]
    )
    return _compile(
        stmt, literal_binds="genres" not in kwargs and "excluded_genres" not in kwargs
    )


def _apply_sort_stmt(sort: str | None) -> str:
    stmt = apply_sort(
        select(Show),
        sort,
        name_col=Show.name,
        year_col=Show.year,
        rating_col=Show.vote_average,
    )
    return _compile(stmt)


def test_no_filters_adds_no_where_clause() -> None:
    sql = _apply_filters()
    assert "WHERE" not in sql


@pytest.mark.parametrize(
    ("query", "expect_where"),
    [
        ("  x  ", True),
        ("  ", False),
        (None, False),
    ],
)
def test_query_filter_strips_and_ilikes(query: str | None, expect_where: bool) -> None:
    sql = _apply_filters(query=query)
    if expect_where:
        assert "ILIKE" in sql
        assert "%%x%%" in sql
    else:
        assert "ILIKE" not in sql


def test_libraries_filter_uses_in() -> None:
    sql = _apply_filters(libraries=["tv", "anime"])
    assert "show.library IN" in sql
    assert "'tv'" in sql
    assert "'anime'" in sql


def test_excluded_libraries_filter_uses_not_in() -> None:
    sql = _apply_filters(excluded_libraries=["kids"])
    assert "show.library NOT IN" in sql
    assert "'kids'" in sql


def test_genres_filter_or_contains() -> None:
    sql = _apply_filters(genres=["Drama", "Comedy"])
    assert " OR " in sql
    assert "show.genres @>" in sql
    assert sql.count("show.genres @>") == 2


def test_excluded_genres_preserves_null_rows() -> None:
    sql = _apply_filters(excluded_genres=["Horror"])
    assert "show.genres IS NULL" in sql
    assert " OR " in sql
    assert "show.genres @>" in sql
    assert "NOT" in sql


def test_decades_filter_uses_year_range() -> None:
    sql = _apply_filters(decades=[1990])
    assert "show.year >= 1990" in sql
    assert "show.year < 2000" in sql


def test_excluded_decades_preserves_null_rows() -> None:
    sql = _apply_filters(excluded_decades=[1980])
    assert "show.year IS NULL" in sql
    assert " OR " in sql
    assert "show.year >= 1980" in sql
    assert "show.year < 1990" in sql
    assert "NOT" in sql


@pytest.mark.parametrize(
    ("sort", "order_fragments"),
    [
        ("name-desc", ["show.name DESC"]),
        ("year-desc", ["show.year DESC NULLS LAST", "show.name ASC"]),
        ("year-asc", ["show.year ASC NULLS LAST", "show.name ASC"]),
        ("rating-desc", ["show.vote_average DESC NULLS LAST", "show.name ASC"]),
        ("rating-asc", ["show.vote_average ASC NULLS LAST", "show.name ASC"]),
    ],
)
def test_apply_sort_known_cases(sort: str, order_fragments: list[str]) -> None:
    sql = _apply_sort_stmt(sort)
    assert "ORDER BY" in sql
    for fragment in order_fragments:
        assert fragment in sql


@pytest.mark.parametrize("sort", [None, "", "unknown"])
def test_apply_sort_default_is_name_asc(sort: str | None) -> None:
    sql = _apply_sort_stmt(sort)
    assert "ORDER BY show.name ASC" in sql
    assert "NULLS LAST" not in sql

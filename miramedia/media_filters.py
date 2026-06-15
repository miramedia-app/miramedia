"""Model-parameterized SQL filter/sort helpers shared by the show and movie
repositories.

Shows and movies expose the same core grid filters (text query, libraries,
genres, decades) and the same sort options, differing only by which mapped
class and columns are involved. This module hosts that shared core so the two
repositories don't carry near-identical clones. Domain-specific predicates
(show airing state, movie downloaded state) stay in the repositories and are
layered on top of these helpers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, not_, or_
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Select

Statement = Select[tuple[Any, ...]]


def apply_list_filters(
    stmt: Statement,
    *,
    name_col: InstrumentedAttribute[Any],
    library_col: InstrumentedAttribute[Any],
    genres_col: InstrumentedAttribute[Any],
    year_col: InstrumentedAttribute[Any],
    query: str | None = None,
    libraries: list[str] | None = None,
    excluded_libraries: list[str] | None = None,
    genres: list[str] | None = None,
    excluded_genres: list[str] | None = None,
    decades: list[int] | None = None,
    excluded_decades: list[int] | None = None,
) -> Statement:
    """Apply the text/library/genre/decade filters shared by list + count.

    ``year_col`` is nullable: decade exclusion must keep rows whose year is
    NULL, otherwise ``not_(...)`` over a NULL comparison evaluates to NULL and
    silently drops them.
    """
    if query and query.strip():
        q = f"%{query.strip()}%"
        stmt = stmt.where(name_col.ilike(q))
    if libraries:
        stmt = stmt.where(library_col.in_(libraries))
    if excluded_libraries:
        stmt = stmt.where(not_(library_col.in_(excluded_libraries)))
    if genres:
        stmt = stmt.where(or_(*(genres_col.contains([g]) for g in genres)))
    if excluded_genres:
        excluded = or_(*(genres_col.contains([g]) for g in excluded_genres))
        stmt = stmt.where(
            or_(genres_col.is_(None), not_(excluded)),
        )
    if decades:
        stmt = stmt.where(
            or_(*(and_(year_col >= d, year_col < d + 10) for d in decades))
        )
    if excluded_decades:
        excluded_decade = or_(
            *(and_(year_col >= d, year_col < d + 10) for d in excluded_decades)
        )
        stmt = stmt.where(
            or_(year_col.is_(None), not_(excluded_decade)),
        )
    return stmt


def apply_sort(
    stmt: Statement,
    sort: str | None,
    *,
    name_col: InstrumentedAttribute[Any],
    year_col: InstrumentedAttribute[Any],
    rating_col: InstrumentedAttribute[Any],
) -> Statement:
    """Apply the shared grid sort options."""
    match sort:
        case "name-desc":
            return stmt.order_by(name_col.desc())
        case "year-desc":
            return stmt.order_by(year_col.desc().nullslast(), name_col.asc())
        case "year-asc":
            return stmt.order_by(year_col.asc().nullslast(), name_col.asc())
        case "rating-desc":
            return stmt.order_by(rating_col.desc().nullslast(), name_col.asc())
        case "rating-asc":
            return stmt.order_by(rating_col.asc().nullslast(), name_col.asc())
        case _:
            return stmt.order_by(name_col.asc())

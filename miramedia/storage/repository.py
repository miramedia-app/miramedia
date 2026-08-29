"""Set-oriented SQL for storage-health counts and bounded file pages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    func,
    literal,
    select,
    true,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Select as SelectType

from miramedia.movies.models import Movie, MovieFile
from miramedia.movies.schemas import MovieFile as MovieFileSchema
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.storage.states import (
    STATE_RANK,
    SqlHealthState,
    corrupt_clause,
    healthy_clause,
    imported_clause,
    orphaned_clause,
    pending_clause,
    unknown_clause,
)
from miramedia.torrents.integrity import Sha1MismatchPageKey

type ClauseFn = Callable[[type[EpisodeFile | MovieFile]], ColumnElement[bool]]


class StorageHealthPage:
    __slots__ = ("keys", "total")

    def __init__(self, keys: list[Sha1MismatchPageKey], total: int) -> None:
        self.keys = keys
        self.total = total


_CLAUSE_BY_STATE: dict[SqlHealthState, ClauseFn] = {
    "corrupt": corrupt_clause,
    "unknown": unknown_clause,
    "orphaned": orphaned_clause,
    "pending": pending_clause,
    "healthy": healthy_clause,
}


def _state_rank_expr(table: type[EpisodeFile | MovieFile]) -> ColumnElement[int]:
    return case(
        (corrupt_clause(table), STATE_RANK["corrupt"]),
        (orphaned_clause(table), STATE_RANK["orphaned"]),
        (pending_clause(table), STATE_RANK["pending"]),
        (unknown_clause(table), STATE_RANK["unknown"]),
        (healthy_clause(table), STATE_RANK["healthy"]),
        else_=STATE_RANK["healthy"],
    )


def _escape_ilike(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _title_pattern(q: str) -> str:
    return f"%{_escape_ilike(q)}%"


class StorageHealthRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _count_stmt(self, table: type[EpisodeFile | MovieFile]) -> SelectType:
        return select(
            func.count().filter(imported_clause(table)).label("imported"),
            func.count().filter(healthy_clause(table)).label("healthy"),
            func.count().filter(unknown_clause(table)).label("unknown"),
            func.count().filter(corrupt_clause(table)).label("corrupt"),
            func.count().filter(orphaned_clause(table)).label("orphaned"),
            func.count().filter(pending_clause(table)).label("pending"),
        ).select_from(table)

    async def _count_table(
        self, table: type[EpisodeFile | MovieFile]
    ) -> dict[str, int]:
        row = (await self.db.execute(self._count_stmt(table))).one()
        return {
            "imported": int(row.imported or 0),
            "healthy": int(row.healthy or 0),
            "unknown": int(row.unknown or 0),
            "corrupt": int(row.corrupt or 0),
            "orphaned": int(row.orphaned or 0),
            "pending": int(row.pending or 0),
        }

    async def count_buckets(self) -> dict[str, int]:
        show_counts = await self._count_table(EpisodeFile)
        movie_counts = await self._count_table(MovieFile)
        return {key: show_counts[key] + movie_counts[key] for key in show_counts}

    def _keys_select(
        self,
        *,
        media_type: Literal["show", "movie"],
        state: SqlHealthState | None,
        q: str | None,
    ) -> Select:
        table: type[EpisodeFile | MovieFile]
        type_sort: int
        if media_type == "show":
            table = EpisodeFile
            type_sort = 0
        else:
            table = MovieFile
            type_sort = 1
        rank = _state_rank_expr(table) if state is None else literal(STATE_RANK[state])
        stmt = select(
            rank.label("state_rank"),
            literal(type_sort).label("type_sort"),
            table.id.label("file_id"),
        )
        predicates: list[ColumnElement[bool]] = []
        if state is not None:
            predicates.append(_CLAUSE_BY_STATE[state](table))
        if q:
            pattern = _title_pattern(q)
            if media_type == "show":
                stmt = (
                    stmt.select_from(EpisodeFile)
                    .join(Episode, EpisodeFile.episode_id == Episode.id)
                    .join(Season, Episode.season_id == Season.id)
                    .join(Show, Season.show_id == Show.id)
                )
                predicates.append(Show.name.ilike(pattern, escape="\\"))
            else:
                stmt = stmt.select_from(MovieFile).join(
                    Movie, MovieFile.movie_id == Movie.id
                )
                predicates.append(Movie.name.ilike(pattern, escape="\\"))
        if predicates:
            stmt = stmt.where(and_(*predicates))
        return stmt

    async def paginate_keys(
        self,
        *,
        offset: int,
        limit: int,
        state: SqlHealthState | None = None,
        media_type: Literal["show", "movie"] | None = None,
        q: str | None = None,
    ) -> StorageHealthPage:
        query = q.strip() if q else None
        if query == "":
            query = None
        parts: list[Select] = []
        if media_type in (None, "show"):
            parts.append(self._keys_select(media_type="show", state=state, q=query))
        if media_type in (None, "movie"):
            parts.append(self._keys_select(media_type="movie", state=state, q=query))
        union = union_all(*parts).subquery("health_keys")
        total_cte = (
            select(func.count().label("total")).select_from(union).cte("health_total")
        )
        page_cte = (
            select(union.c.state_rank, union.c.type_sort, union.c.file_id)
            .order_by(union.c.state_rank, union.c.file_id, union.c.type_sort)
            .offset(offset)
            .limit(limit)
            .cte("health_page")
        )
        stmt = (
            select(
                total_cte.c.total,
                page_cte.c.state_rank,
                page_cte.c.type_sort,
                page_cte.c.file_id,
            )
            .select_from(total_cte.outerjoin(page_cte, true()))
            .order_by(
                page_cte.c.state_rank.asc().nulls_last(),
                page_cte.c.file_id.asc().nulls_last(),
                page_cte.c.type_sort.asc().nulls_last(),
            )
        )
        rows = (await self.db.execute(stmt)).all()
        if not rows:
            return StorageHealthPage(keys=[], total=0)
        total = int(rows[0][0])
        keys = [
            Sha1MismatchPageKey(
                media_type="show" if type_sort == 0 else "movie",
                file_id=file_id,
            )
            for _total, _rank, type_sort, file_id in rows
            if file_id is not None
        ]
        return StorageHealthPage(keys=keys, total=total)

    async def get_episode_files_by_ids(
        self, file_ids: list[UUID]
    ) -> dict[UUID, EpisodeFileSchema]:
        if not file_ids:
            return {}
        stmt = select(EpisodeFile).where(EpisodeFile.id.in_(file_ids))
        rows = (await self.db.execute(stmt)).scalars().all()
        return {row.id: EpisodeFileSchema.model_validate(row) for row in rows}

    async def get_movie_files_by_ids(
        self, file_ids: list[UUID]
    ) -> dict[UUID, MovieFileSchema]:
        if not file_ids:
            return {}
        stmt = select(MovieFile).where(MovieFile.id.in_(file_ids))
        rows = (await self.db.execute(stmt)).scalars().all()
        return {row.id: MovieFileSchema.model_validate(row) for row in rows}

    async def list_title_library_names(self) -> list[str]:
        show_q = select(Show.library)
        movie_q = select(Movie.library)
        stmt = union_all(show_q, movie_q)
        rows = (await self.db.execute(stmt)).all()
        names: set[str] = set()
        for (raw,) in rows:
            name = (raw or "").strip()
            if name and name != "Default":
                names.add(name)
        return sorted(names)

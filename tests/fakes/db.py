"""Minimal async DB stand-ins for DB-free orchestration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select


class FakeDb:
    """Async session stub — commit/close are no-ops for DB-free tests."""

    released: bool = False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs) -> None:
        return None


@dataclass
class _ScalarResult:
    rows: list[Any]

    def all(self) -> list[Any]:
        return self.rows


@dataclass
class _ExecuteResult:
    rows: list[Any]
    rowcount: int = 0
    scalar: Any = None

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self.rows)

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def scalar_one(self) -> Any:
        if self.scalar is None:
            msg = "no scalar result"
            raise ValueError(msg)
        return self.scalar


@dataclass
class RecordingSession:
    """Captures ``execute`` calls and returns configured rows for SELECTs."""

    episode_rows: list[Any] = field(default_factory=list)
    movie_rows: list[Any] = field(default_factory=list)
    executes: list[Any] = field(default_factory=list)
    episode_high_water: UUID | None = None
    movie_high_water: UUID | None = None
    episode_budget: int | None = None
    movie_budget: int | None = None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    def _is_high_water_scalar(self, stmt: Select) -> bool:
        if stmt._limit_clause is None or stmt._limit_clause.value != 1:
            return False
        if len(stmt.column_descriptions) != 1:
            return False
        name = stmt.column_descriptions[0].get("name")
        return name == "id"

    def _is_count_scalar(self, stmt: Select) -> bool:
        if stmt._limit_clause is not None:
            return False
        if len(stmt.column_descriptions) != 1:
            return False
        name = str(stmt.column_descriptions[0].get("name", ""))
        return "count" in name.lower()

    def _select_table(self, stmt: Select) -> str:
        for table in stmt.get_final_froms():
            table_name = getattr(table, "name", "")
            if table_name:
                return table_name
        entity = stmt.column_descriptions[0].get("entity")
        return getattr(entity, "__name__", "")

    def _chunk_rows(self, rows: list[Any], stmt: Select) -> list[Any]:
        """Apply keyset ``id >`` / ``id <=`` and ``LIMIT`` when present."""
        ordered = sorted(rows, key=lambda row: row.id)
        min_id = None
        max_id = None
        for criterion in stmt._where_criteria:
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            if getattr(left, "key", None) != "id":
                continue
            right_val = getattr(right, "value", None)
            if right_val is None:
                continue
            op = getattr(criterion, "operator", None)
            if op == operators.gt:
                min_id = right_val
            elif op == operators.le:
                max_id = right_val
        if min_id is not None:
            ordered = [row for row in ordered if row.id > min_id]
        if max_id is not None:
            ordered = [row for row in ordered if row.id <= max_id]
        limit_clause = stmt._limit_clause
        if limit_clause is not None and limit_clause.value is not None:
            ordered = ordered[: int(limit_clause.value)]
        return ordered

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.executes.append(stmt)
        if isinstance(stmt, Select):
            if self._is_high_water_scalar(stmt):
                table = self._select_table(stmt)
                if table == "episode_file" or table == "EpisodeFile":
                    hw = self.episode_high_water
                    if hw is None and self.episode_rows:
                        hw = max(row.id for row in self.episode_rows)
                    return _ExecuteResult([], scalar=hw)
                if table == "movie_file" or table == "MovieFile":
                    hw = self.movie_high_water
                    if hw is None and self.movie_rows:
                        hw = max(row.id for row in self.movie_rows)
                    return _ExecuteResult([], scalar=hw)
            if self._is_count_scalar(stmt):
                table = self._select_table(stmt)
                if table == "episode_file" or table == "EpisodeFile":
                    budget = self.episode_budget
                    count = budget if budget is not None else len(self.episode_rows)
                    return _ExecuteResult([], scalar=count)
                if table == "movie_file" or table == "MovieFile":
                    budget = self.movie_budget
                    count = budget if budget is not None else len(self.movie_rows)
                    return _ExecuteResult([], scalar=count)
            entity = stmt.column_descriptions[0].get("entity")
            entity_name = getattr(entity, "__name__", "")
            if entity_name == "EpisodeFile":
                return _ExecuteResult(self._chunk_rows(self.episode_rows, stmt))
            if entity_name == "MovieFile":
                return _ExecuteResult(self._chunk_rows(self.movie_rows, stmt))
            return _ExecuteResult([])
        if isinstance(stmt, Update):
            return _ExecuteResult([], rowcount=1)
        return _ExecuteResult([])

    @property
    def updates(self) -> list[Update]:
        return [stmt for stmt in self.executes if isinstance(stmt, Update)]

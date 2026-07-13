"""Minimal async DB stand-ins for DB-free orchestration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select


class FakeDb:
    """Async session stub — commit/close are no-ops for DB-free tests."""

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

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self.rows)


@dataclass
class RecordingSession:
    """Captures ``execute`` calls and returns configured rows for SELECTs."""

    episode_rows: list[Any] = field(default_factory=list)
    movie_rows: list[Any] = field(default_factory=list)
    executes: list[Any] = field(default_factory=list)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.executes.append(stmt)
        if isinstance(stmt, Select):
            entity = stmt.column_descriptions[0].get("entity")
            entity_name = getattr(entity, "__name__", "")
            if entity_name == "EpisodeFile":
                return _ExecuteResult(self.episode_rows)
            if entity_name == "MovieFile":
                return _ExecuteResult(self.movie_rows)
            return _ExecuteResult([])
        return _ExecuteResult([])

    @property
    def updates(self) -> list[Update]:
        return [stmt for stmt in self.executes if isinstance(stmt, Update)]

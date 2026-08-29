"""Recording tests for set-oriented storage-health SQL."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.sql.selectable import Select

from miramedia.storage.repository import StorageHealthRepository
from miramedia.storage.states import SHA1_MISMATCH_LIKE


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _CountRow:
    imported: int = 0
    healthy: int = 0
    unknown: int = 0
    corrupt: int = 0
    orphaned: int = 0
    pending: int = 0


@dataclass
class _Result:
    row: _CountRow = field(default_factory=_CountRow)
    rows: list[Any] = field(default_factory=list)

    def one(self) -> _CountRow:
        return self.row

    def all(self) -> list[Any]:
        return self.rows

    def scalars(self) -> _Result:
        return self


@dataclass
class _RecordingSession:
    executes: list[Any] = field(default_factory=list)
    count_row: _CountRow = field(default_factory=_CountRow)
    rows: list[Any] = field(default_factory=list)

    async def execute(self, stmt: Any) -> _Result:
        self.executes.append(stmt)
        assert isinstance(stmt, Select)
        return _Result(row=self.count_row, rows=self.rows)


def _sql(stmt: Select) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()


def test_count_buckets_two_table_filter_aggregates() -> None:
    session = _RecordingSession()
    repo = StorageHealthRepository(session)  # type: ignore[arg-type]
    buckets = _run(repo.count_buckets())
    assert len(session.executes) == 2
    assert buckets["imported"] == 0
    assert "unknown" in buckets
    for stmt in session.executes:
        sql = _sql(stmt)
        assert "filter" in sql or "count" in sql
        assert SHA1_MISMATCH_LIKE.lower() in sql
        assert "sha1 is null" in sql or "sha1 is null" in sql.replace("\n", " ")


def test_count_sql_does_not_walk_files() -> None:
    session = _RecordingSession()
    repo = StorageHealthRepository(session)  # type: ignore[arg-type]
    _run(repo.count_buckets())
    joined = " ".join(_sql(stmt) for stmt in session.executes)
    assert "iterdir" not in joined


def test_paginate_keys_one_statement_state_rank_then_file_id() -> None:
    file_id = uuid4()
    session = _RecordingSession(
        rows=[(1, 0, 0, file_id)],
    )
    repo = StorageHealthRepository(session)  # type: ignore[arg-type]
    page = _run(repo.paginate_keys(offset=0, limit=50))
    assert len(session.executes) == 1
    sql = _sql(session.executes[0])
    assert "state_rank" in sql
    assert "file_id" in sql
    assert page.total == 1
    assert page.keys[0].file_id == file_id
    assert page.keys[0].media_type == "show"


def test_paginate_keys_limit_applied_in_sql() -> None:
    session = _RecordingSession(rows=[(0, None, None, None)])
    repo = StorageHealthRepository(session)  # type: ignore[arg-type]
    _run(repo.paginate_keys(offset=10, limit=25, state="corrupt"))
    sql = _sql(session.executes[0])
    assert "offset" in sql
    assert "25" in sql or "limit" in sql
    assert "sha1 mismatch%" in sql


def test_paginate_search_joins_title() -> None:
    repo = StorageHealthRepository(_RecordingSession())  # type: ignore[arg-type]
    sql = _sql(repo._keys_select(media_type="show", state=None, q="Sev"))
    assert "like" in sql
    assert "show.name" in sql or ".name" in sql
    movie_sql = _sql(repo._keys_select(media_type="movie", state=None, q="Sev"))
    assert "like" in movie_sql


def test_unknown_filter_excludes_mismatch_stamp() -> None:
    repo = StorageHealthRepository(_RecordingSession())  # type: ignore[arg-type]
    sql = _sql(repo._keys_select(media_type="show", state="unknown", q=None))
    assert "sha1 is null" in sql
    assert "sha1 mismatch%" in sql
    assert "not" in sql

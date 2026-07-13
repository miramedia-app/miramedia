"""Recording tests for single-statement integrity mismatch pagination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.sql.selectable import Select

from miramedia.torrents.repository import TorrentRepository


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _PaginationExecuteResult:
    rows: list[Any]

    def all(self) -> list[Any]:
        return self.rows


@dataclass
class _PaginationRecordingSession:
    executes: list[Any] = field(default_factory=list)
    rows: list[tuple[int, int | None, UUID | None]] = field(default_factory=list)

    async def execute(self, stmt: Any) -> _PaginationExecuteResult:
        self.executes.append(stmt)
        assert isinstance(stmt, Select)
        return _PaginationExecuteResult(self.rows)


def test_paginate_sha1_mismatch_keys_empty_page_one_statement() -> None:
    session = _PaginationRecordingSession(
        rows=[(0, None, None)],
    )
    repo = TorrentRepository(session)  # type: ignore[arg-type]

    page = _run(repo.paginate_sha1_mismatch_keys(offset=99, limit=50))

    assert len(session.executes) == 1
    assert page.keys == []
    assert page.total == 0


def test_paginate_sha1_mismatch_keys_nonempty_page_one_statement() -> None:
    file_id = uuid4()
    movie_id = uuid4()
    session = _PaginationRecordingSession(
        rows=[(2, 0, file_id), (2, 1, movie_id)],
    )
    repo = TorrentRepository(session)  # type: ignore[arg-type]

    page = _run(repo.paginate_sha1_mismatch_keys(offset=0, limit=10))

    assert len(session.executes) == 1
    stmt = session.executes[0]
    assert stmt._order_by_clauses
    order_sql = " ".join(
        str(clause.compile(compile_kwargs={"literal_binds": True}))
        for clause in stmt._order_by_clauses
    )
    assert "type_sort" in order_sql
    assert "file_id" in order_sql
    assert page.total == 2
    assert len(page.keys) == 2
    assert page.keys[0].media_type == "show"
    assert page.keys[0].file_id == file_id
    assert page.keys[1].media_type == "movie"
    assert page.keys[1].file_id == movie_id


def test_paginate_sha1_mismatch_keys_preserves_sql_result_order() -> None:
    show_a = uuid4()
    show_b = uuid4()
    movie_a = uuid4()
    session = _PaginationRecordingSession(
        rows=[
            (3, 0, show_a),
            (3, 0, show_b),
            (3, 1, movie_a),
        ],
    )
    repo = TorrentRepository(session)  # type: ignore[arg-type]

    page = _run(repo.paginate_sha1_mismatch_keys(offset=0, limit=10))

    assert [key.file_id for key in page.keys] == [show_a, show_b, movie_a]
    assert [key.media_type for key in page.keys] == ["show", "show", "movie"]

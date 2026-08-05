"""Unit tests for show progress refresh semantics and SQL shape."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from miramedia.media_state import (
    _update_show_progress_counters,
    refresh_show_progress,
)
from miramedia.shows.models import Show


def test_set_based_show_update_compiles_single_update_from_subquery() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    asyncio.run(_update_show_progress_counters(db))

    assert db.execute.await_count == 1
    stmt = db.execute.await_args_list[0].args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "update show" in sql.lower()
    assert "episode_stats" in sql.lower()
    assert "show_progress" in sql.lower()


def test_scoped_show_refresh_issues_one_show_update() -> None:
    db = AsyncMock()
    show_id = uuid.uuid4()
    db.execute = AsyncMock(return_value=MagicMock())

    asyncio.run(refresh_show_progress(db, show_id=show_id))

    show_update_count = sum(
        1
        for call in db.execute.await_args_list
        if call.args[0].table.name == Show.__tablename__
    )
    assert show_update_count == 1


def test_unscoped_show_refresh_issues_one_show_update() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())

    asyncio.run(refresh_show_progress(db))

    show_update_count = sum(
        1
        for call in db.execute.await_args_list
        if call.args[0].table.name == Show.__tablename__
    )
    assert show_update_count == 1

"""Unit tests for upcoming library window, merge, and title formatting."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dt_time
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from miramedia.upcoming.repository import UpcomingRepository
from miramedia.upcoming.schemas import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    MAX_WINDOW_DAYS,
    UPCOMING_HARD_CAP,
    UpcomingItem,
    UpcomingWindow,
)
from miramedia.upcoming.service import (
    UpcomingService,
    explicit_window,
    local_today,
    merge_upcoming,
    upcoming_window,
)
from tests.fakes import run_async


def _fetch_upcoming_items(db, window, *, limit=UPCOMING_HARD_CAP):
    service = UpcomingService(UpcomingRepository(db))
    return service.fetch_upcoming_items(window, limit=limit)


def test_upcoming_window_default_today_next30() -> None:
    today = date(2026, 8, 7)
    window = upcoming_window(today)
    assert window.today == today
    assert window.start == today
    assert window.end == date(2026, 9, 6)


def test_local_today_uses_server_local_calendar_not_utc_midnight() -> None:
    # 2026-08-07 01:00 UTC is still 2026-08-06 in US/Pacific.
    now = datetime(2026, 8, 7, 1, 0, tzinfo=UTC).astimezone(
        timezone(timedelta(hours=-7))
    )
    assert local_today(now) == date(2026, 8, 6)


def test_merge_upcoming_sorts_by_date_then_title_and_caps() -> None:
    early = date(2026, 8, 1)
    mid = date(2026, 8, 2)
    items = [
        UpcomingItem(
            media_type="movie",
            id=uuid4(),
            title="Zeta",
            date=mid,
            poster_id=uuid4(),
        ),
        UpcomingItem(
            media_type="movie",
            id=uuid4(),
            title="alpha",
            date=mid,
            poster_id=uuid4(),
        ),
        UpcomingItem(
            media_type="episode",
            id=uuid4(),
            title="Beta Show - S01E01",
            date=early,
            poster_id=uuid4(),
            show_id=uuid4(),
        ),
    ]
    merged = merge_upcoming(items, limit=2)
    assert [item.title for item in merged] == ["Beta Show - S01E01", "alpha"]
    assert len(merge_upcoming(items, limit=UPCOMING_HARD_CAP)) == 3


def test_merge_upcoming_empty_limit() -> None:
    real = UpcomingItem(
        media_type="movie",
        id=uuid4(),
        title="Real",
        date=date(2026, 8, 7),
        poster_id=uuid4(),
    )
    assert merge_upcoming([real], limit=0) == []


def test_fetch_upcoming_items_merges_episodes_and_movies() -> None:
    show_id = uuid4()
    episode_id = uuid4()
    movie_id = uuid4()
    episode_result = MagicMock()
    episode_result.all.return_value = [
        (
            episode_id,
            "Pilot",
            date(2026, 8, 10),
            dt_time(21, 0),
            1,
            False,
            1,
            show_id,
            "Cool Show",
        )
    ]
    movie_result = MagicMock()
    movie_result.all.return_value = [
        (movie_id, "Alpha Film", date(2026, 8, 9), True),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[episode_result, movie_result])

    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )
    items, truncated = run_async(_fetch_upcoming_items(db, window))

    assert [item.title for item in items] == [
        "Alpha Film",
        "Cool Show - S01E01 - Pilot",
    ]
    assert items[0].media_type == "movie"
    assert items[0].poster_id == movie_id
    assert items[0].downloaded is True
    assert items[1].media_type == "episode"
    assert items[1].poster_id == show_id
    assert items[1].show_id == show_id
    assert items[1].air_time == dt_time(21, 0)
    assert items[0].air_time is None  # movies carry no time
    assert db.execute.await_count == 2
    assert truncated is False


def _episode_row(
    episode_id: object,
    *,
    air_date: date,
    title: str = "Ep",
    episode_number: int = 1,
    season_number: int = 1,
    show_id: object | None = None,
    show_name: str = "Show",
    air_time: object | None = None,
) -> tuple[object, ...]:
    return (
        episode_id,
        title,
        air_date,
        air_time,
        episode_number,
        False,
        season_number,
        show_id or uuid4(),
        show_name,
    )


def test_fetch_upcoming_items_not_truncated_when_both_sources_under_cap() -> None:
    episode_result = MagicMock()
    episode_result.all.return_value = [
        _episode_row(uuid4(), air_date=date(2026, 8, 10)),
    ]
    movie_result = MagicMock()
    movie_result.all.return_value = [
        (uuid4(), "Alpha Film", date(2026, 8, 9), True),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[episode_result, movie_result])
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )

    items, truncated = run_async(_fetch_upcoming_items(db, window, limit=5))

    assert len(items) == 2
    assert truncated is False


def test_fetch_upcoming_items_clamps_movies_past_episode_cutoff_when_episodes_truncated() -> (
    None
):
    limit = 3
    episode_rows = [
        _episode_row(
            uuid4(),
            air_date=date(2026, 8, i),
            title=f"E{i}",
            episode_number=i,
        )
        for i in range(1, limit + 1)
    ]
    late_movie_id = uuid4()
    episode_result = MagicMock()
    episode_result.all.return_value = episode_rows
    movie_result = MagicMock()
    movie_result.all.return_value = [
        (uuid4(), "Early Film", date(2026, 8, 2), False),
        (late_movie_id, "Late Film", date(2026, 8, 10), False),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[episode_result, movie_result])
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )

    items, truncated = run_async(_fetch_upcoming_items(db, window, limit=limit))

    assert truncated is True
    titles = [item.title for item in items]
    assert "Early Film" in titles
    assert "Late Film" not in titles
    assert "E3" not in titles
    assert all(item.date < date(2026, 8, 3) for item in items)


def test_fetch_upcoming_items_keeps_movie_before_truncated_episode_cutoff() -> None:
    limit = 2
    episode_rows = [
        _episode_row(uuid4(), air_date=date(2026, 8, 5), title="E1", episode_number=1),
        _episode_row(uuid4(), air_date=date(2026, 8, 8), title="E2", episode_number=2),
    ]
    early_movie_id = uuid4()
    episode_result = MagicMock()
    episode_result.all.return_value = episode_rows
    movie_result = MagicMock()
    movie_result.all.return_value = [
        (early_movie_id, "Before Cutoff", date(2026, 8, 4), False),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[episode_result, movie_result])
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )

    items, truncated = run_async(_fetch_upcoming_items(db, window, limit=limit))

    assert truncated is True
    assert any(item.id == early_movie_id for item in items)


def test_fetch_upcoming_items_drops_partial_final_day() -> None:
    limit = 3
    episode_rows = [
        _episode_row(uuid4(), air_date=date(2026, 8, 1), title="E1", episode_number=1),
        _episode_row(uuid4(), air_date=date(2026, 8, 2), title="E2", episode_number=2),
        _episode_row(uuid4(), air_date=date(2026, 8, 2), title="E3", episode_number=3),
    ]
    episode_result = MagicMock()
    episode_result.all.return_value = episode_rows
    movie_result = MagicMock()
    movie_result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[episode_result, movie_result])
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )

    items, truncated = run_async(_fetch_upcoming_items(db, window, limit=limit))

    assert truncated is True
    assert len(items) == 1
    assert items[0].date == date(2026, 8, 1)
    assert items[0].episode_number == 1


def test_fetch_upcoming_items_single_date_fallback_keeps_truncated_prefix() -> None:
    limit = 2
    episode_rows = [
        _episode_row(uuid4(), air_date=date(2026, 8, 5), title="E1", episode_number=1),
        _episode_row(uuid4(), air_date=date(2026, 8, 5), title="E2", episode_number=2),
    ]
    episode_result = MagicMock()
    episode_result.all.return_value = episode_rows
    movie_result = MagicMock()
    movie_result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[episode_result, movie_result])
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )

    items, truncated = run_async(_fetch_upcoming_items(db, window, limit=limit))

    assert truncated is True
    assert len(items) == 2
    assert all(item.date == date(2026, 8, 5) for item in items)
    assert {item.episode_number for item in items} == {1, 2}


def test_episode_query_binds_window_and_orders() -> None:
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    repo = UpcomingRepository(db)

    asyncio.run(repo.fetch_episode_rows(window, limit=7))

    stmt = db.execute.await_args_list[0].args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "episode.air_date >=" in sql
    assert "episode.air_date <=" in sql
    assert sql.count("skipped IS false") == 3
    assert "ORDER BY episode.air_date ASC" in sql
    assert "show.name ASC" in sql
    assert " LIMIT " in sql


def test_movie_query_binds_window_and_orders() -> None:
    window = UpcomingWindow(
        start=date(2026, 8, 1), end=date(2026, 9, 1), today=date(2026, 8, 7)
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    repo = UpcomingRepository(db)

    asyncio.run(repo.fetch_movie_rows(window, limit=7))

    stmt = db.execute.await_args_list[0].args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "movie.release_date >=" in sql
    assert "movie.release_date <=" in sql
    assert "movie.skipped IS false" in sql
    assert "ORDER BY movie.release_date ASC" in sql
    assert "movie.name ASC" in sql
    assert " LIMIT " in sql


def test_explicit_window_honors_both_bounds() -> None:
    window = explicit_window(date(2026, 1, 1), date(2026, 1, 31))
    assert window.start == date(2026, 1, 1)
    assert window.end == date(2026, 1, 31)


def test_explicit_window_fills_missing_side_with_default_offset() -> None:
    today = local_today()
    start_only = explicit_window(date(2026, 1, 1), None)
    assert start_only.end == today + timedelta(days=DEFAULT_FUTURE_DAYS)
    end_only = explicit_window(None, date(2026, 12, 31))
    assert end_only.start == today - timedelta(days=DEFAULT_PAST_DAYS)


def test_explicit_window_clamps_span_to_max() -> None:
    start = date(2026, 1, 1)
    window = explicit_window(start, start + timedelta(days=MAX_WINDOW_DAYS + 500))
    assert window.end == start + timedelta(days=MAX_WINDOW_DAYS)


def test_explicit_window_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="earlier than start"):
        explicit_window(date(2026, 2, 1), date(2026, 1, 1))

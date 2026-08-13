"""Plan 232: lightweight IMDb duplicate lookup for show add."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.selectable import Select

from miramedia.shows.models import Show
from miramedia.shows.repository import (
    ShowRepository,
    _full_show_eager_loads,
    _show_summary_eager_loads,
)
from miramedia.shows.schemas import Episode, EpisodeNumber, Season, SeasonNumber, ShowId
from miramedia.shows.service import ShowService
from tests.fakes import run_async
from tests.fakes.repositories import make_show


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _ScalarFirstResult:
    row: Any | None

    def scalars(self) -> _ScalarFirstResult:
        return self

    def first(self) -> Any | None:
        return self.row


@dataclass
class _ImdbDuplicateRecordingSession:
    executes: list[Any] = field(default_factory=list)
    row: Any | None = None
    raise_on_execute: SQLAlchemyError | None = None

    async def execute(self, stmt: Any) -> _ScalarFirstResult:
        self.executes.append(stmt)
        assert isinstance(stmt, Select)
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        return _ScalarFirstResult(self.row)


def _show_row(
    *, imdb_id: str = "tt0903747", name: str = "Breaking Bad"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        overview="",
        year=2008,
        ended=False,
        external_id=imdb_id,
        metadata_provider="native",
        continuous_download=None,
        skipped=False,
        library="Default",
        original_language="en",
        imdb_id=imdb_id,
        vote_average=9.5,
        content_rating=None,
        genres=["Drama"],
        cast=["Bryan Cranston"],
        preferred_quality=None,
        preferred_codec=None,
        subtitle_languages=None,
        last_metadata_check=None,
        metadata_failure_backoff_until=None,
        auto_download_backoff_until=None,
        wanted_episode_count=62,
        downloaded_episode_count=62,
        list_progress_status="complete",
        seasons=[],
    )


def _summary_lookup_stmt(imdb_id: str) -> Select:
    return (
        select(Show)
        .where(Show.imdb_id == imdb_id)
        .options(*_show_summary_eager_loads())
    )


def _loader_path(stmt: Select) -> str:
    return str(stmt._with_options[0].path)


def _assert_uses_summary_load(stmt: Select) -> None:
    assert _loader_path(stmt) == _loader_path(_summary_lookup_stmt("tt0903747"))
    assert _loader_path(stmt) != _loader_path(
        select(Show).options(*_full_show_eager_loads())
    )


def _large_show_fixture(*, imdb_id: str = "tt0903747") -> Any:
    """Show with many seasons/episodes/files for descendant-independent assertions."""
    show_id = ShowId(uuid4())
    seasons: list[Season] = []
    for season_number in range(1, 6):
        episodes = [
            Episode(
                number=EpisodeNumber(episode_number),
                title=f"S{season_number:02d}E{episode_number:02d}",
            )
            for episode_number in range(1, 25)
        ]
        seasons.append(
            Season(
                show_id=show_id,
                number=SeasonNumber(season_number),
                episodes=episodes,
            )
        )
    return make_show(name="Large Library Show").model_copy(
        update={
            "id": show_id,
            "imdb_id": imdb_id,
            "external_id": imdb_id,
            "metadata_provider": "native",
            "seasons": seasons,
            "wanted_episode_count": 120,
            "downloaded_episode_count": 120,
        }
    )


def test_show_imdb_duplicate_lookup_issues_single_query() -> None:
    session = _ImdbDuplicateRecordingSession(row=_show_row())
    repo = ShowRepository(session)  # type: ignore[arg-type]

    result = _run(repo.show_exists_by_imdb_id("tt0903747"))

    assert len(session.executes) == 1
    assert result is not None
    assert result.imdb_id == "tt0903747"
    assert result.name == "Breaking Bad"
    assert result.seasons == []


def test_show_imdb_duplicate_lookup_uses_summary_loader_not_full_tree() -> None:
    session = _ImdbDuplicateRecordingSession(row=_show_row())
    repo = ShowRepository(session)  # type: ignore[arg-type]

    _run(repo.show_exists_by_imdb_id("tt0903747"))

    stmt = session.executes[0]
    _assert_uses_summary_load(stmt)


def test_show_imdb_duplicate_bounded_query_independent_of_tree_size() -> None:
    small_session = _ImdbDuplicateRecordingSession(row=_show_row())
    large_session = _ImdbDuplicateRecordingSession(
        row=_show_row(name="Large Library Show")
    )
    small_repo = ShowRepository(small_session)  # type: ignore[arg-type]
    large_repo = ShowRepository(large_session)  # type: ignore[arg-type]

    _run(small_repo.show_exists_by_imdb_id("tt0903747"))
    _run(large_repo.show_exists_by_imdb_id("tt0903747"))

    assert len(small_session.executes) == 1
    assert len(large_session.executes) == 1
    _assert_uses_summary_load(small_session.executes[0])
    _assert_uses_summary_load(large_session.executes[0])


def test_show_imdb_duplicate_lookup_returns_none_when_missing() -> None:
    session = _ImdbDuplicateRecordingSession(row=None)
    repo = ShowRepository(session)  # type: ignore[arg-type]

    result = _run(repo.show_exists_by_imdb_id("tt0000000"))

    assert result is None
    assert len(session.executes) == 1


def test_show_imdb_duplicate_lookup_db_error_returns_none() -> None:
    session = _ImdbDuplicateRecordingSession(
        raise_on_execute=SQLAlchemyError("db down")
    )
    repo = ShowRepository(session)  # type: ignore[arg-type]

    result = _run(repo.show_exists_by_imdb_id("tt0903747"))

    assert result is None
    assert len(session.executes) == 1


def test_add_show_imdb_duplicate_returns_existing_show() -> None:
    full_show = _large_show_fixture()
    summary = full_show.model_copy(update={"seasons": []})
    show_repo = MagicMock()
    show_repo.show_exists_by_imdb_id = AsyncMock(return_value=summary)
    show_repo.get_show_by_id = AsyncMock(return_value=full_show)
    show_repo.save_show = AsyncMock()
    show_repo.db = MagicMock()

    provider = MagicMock()
    provider.name = "tmdb"
    provider.get_show_metadata.return_value = make_show(
        name="TMDB Copy",
        season_number=1,
        episode_number=1,
    ).model_copy(
        update={
            "imdb_id": "tt0903747",
            "external_id": "77169",
            "metadata_provider": "tmdb",
        }
    )

    svc = ShowService(show_repo, MagicMock(), None, None)
    result = run_async(svc.add_show("77169", provider))

    assert result is full_show
    assert len(result.seasons) == 5
    show_repo.save_show.assert_not_called()
    show_repo.show_exists_by_imdb_id.assert_awaited_once_with("tt0903747")
    show_repo.get_show_by_id.assert_awaited_once_with(show_id=summary.id)


def test_add_show_imdb_absent_skips_duplicate_lookup() -> None:
    show_repo = MagicMock()
    show_repo.show_exists_by_imdb_id = AsyncMock()
    show_repo.save_show = AsyncMock(
        side_effect=lambda show: show.model_copy(update={"id": ShowId(uuid4())})
    )
    show_repo.db = MagicMock()

    incoming = make_show(name="No IMDb").model_copy(update={"imdb_id": None})
    provider = MagicMock()
    provider.name = "tmdb"
    provider.get_show_metadata.return_value = incoming

    svc = ShowService(show_repo, MagicMock(), None, None)
    run_async(svc.add_show("77169", provider))

    show_repo.show_exists_by_imdb_id.assert_not_called()
    show_repo.save_show.assert_awaited_once()


def test_add_show_imdb_unknown_proceeds_to_save() -> None:
    show_repo = MagicMock()
    show_repo.show_exists_by_imdb_id = AsyncMock(return_value=None)
    saved = make_show(name="Fresh Show").model_copy(update={"imdb_id": "tt1111111"})
    show_repo.save_show = AsyncMock(return_value=saved)
    show_repo.db = MagicMock()

    provider = MagicMock()
    provider.name = "tmdb"
    provider.get_show_metadata.return_value = saved
    provider.download_show_poster_image = MagicMock()

    svc = ShowService(show_repo, MagicMock(), None, None)
    result = run_async(svc.add_show("99999", provider))

    assert result is saved
    show_repo.show_exists_by_imdb_id.assert_awaited_once_with("tt1111111")
    show_repo.save_show.assert_awaited_once()

"""Air dates must survive show and season persistence."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import Episode, EpisodeNumber, Season, SeasonNumber
from tests.fakes import run_async
from tests.fakes.repositories import make_show


class _StopCommitError(Exception):
    pass


def _empty_result() -> MagicMock:
    result = MagicMock()
    result.unique.return_value.scalar_one_or_none.return_value = None
    result.scalar_one_or_none.return_value = None
    return result


def test_save_show_keeps_episode_air_date() -> None:
    expected = date(2026, 1, 15)
    show = make_show(air_date=expected)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_empty_result())
    db.commit = AsyncMock(side_effect=_StopCommitError)
    repository = ShowRepository(db)

    with pytest.raises(_StopCommitError):
        run_async(repository.save_show(show))

    saved_show = db.add.call_args.args[0]
    assert saved_show.seasons[0].episodes[0].air_date == expected


def test_add_season_keeps_episode_air_date() -> None:
    expected = date(2027, 1, 1)
    show = make_show()
    season = Season(
        number=SeasonNumber(2),
        episodes=[
            Episode(
                number=EpisodeNumber(1),
                title="Season premiere",
                air_date=expected,
            )
        ],
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=object())
    db.execute = AsyncMock(return_value=_empty_result())
    db.commit = AsyncMock(side_effect=_StopCommitError)
    repository = ShowRepository(db)

    with pytest.raises(_StopCommitError):
        run_async(repository.add_season_to_show(show.id, season))

    saved_season = db.add.call_args.args[0]
    assert saved_season.episodes[0].air_date == expected


def test_save_show_existing_row_backfills_episode_air_date() -> None:
    expected = date(2026, 1, 15)
    show = make_show(air_date=expected)
    existing_episode = MagicMock(number=1, air_date=None)
    existing_season = MagicMock(number=1, episodes=[existing_episode])
    existing_show = MagicMock(seasons=[existing_season])
    result = _empty_result()
    result.unique.return_value.scalar_one_or_none.return_value = existing_show
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock(side_effect=_StopCommitError)
    repository = ShowRepository(db)

    with pytest.raises(_StopCommitError):
        run_async(repository.save_show(show))

    assert existing_episode.air_date == expected

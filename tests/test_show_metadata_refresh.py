"""DB-free tests for metadata refresh bulk episode attribute writes."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from miramedia.shows.schemas import (
    Episode,
    EpisodeAttributeChange,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from tests.fakes import build_show_service, run_async


def _episode(
    number: int,
    *,
    title: str = "Pilot",
    overview: str | None = None,
    air_date: date | None = None,
) -> Episode:
    return Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(number),
        title=title,
        overview=overview,
        air_date=air_date,
    )


def _show_with_episodes(*episodes: Episode) -> Show:
    show_id = ShowId(uuid.uuid4())
    season = Season(
        id=SeasonId(uuid.uuid4()),
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=list(episodes),
    )
    return Show(
        id=show_id,
        name="Test Show",
        overview="",
        year=2020,
        external_id="ext-1",
        metadata_provider="native",
        seasons=[season],
    )


def _run_refresh(
    db_show: Show,
    fresh_show: Show,
) -> MagicMock:
    show_repo = MagicMock()
    show_repo.db = MagicMock()
    show_repo.update_show_attributes = AsyncMock()
    show_repo.update_episodes_attributes_bulk = AsyncMock()
    show_repo.add_episodes_to_season = AsyncMock()
    show_repo.add_season_to_show = AsyncMock()
    show_repo.get_show_by_id = AsyncMock(return_value=db_show)

    svc, _, _ = build_show_service(show_repo=show_repo)  # type: ignore[arg-type]

    metadata_provider = MagicMock()
    metadata_provider.name = "native"
    metadata_provider.storage_path = "/var/lib/miramedia/posters"

    with patch("miramedia.metadata.utils.poster_exists", return_value=True):
        run_async(
            svc.update_show_metadata(
                db_show=db_show,
                metadata_provider=metadata_provider,
                fresh_show_data=fresh_show,
            )
        )

    return show_repo


class TestUpdateShowMetadataBulkWrites:
    def test_many_changed_episodes_use_one_bulk_call(self) -> None:
        episodes = [
            _episode(
                number, title=f"E{number}", overview="A", air_date=date(2020, 1, number)
            )
            for number in (1, 2, 3, 4, 5)
        ]
        db_show = _show_with_episodes(*episodes)
        fresh_show = db_show.model_copy(deep=True)
        for index, episode in enumerate(fresh_show.seasons[0].episodes):
            fresh_show.seasons[0].episodes[index] = episode.model_copy(
                update={"title": f"E{episode.number} revised"}
            )

        show_repo = _run_refresh(db_show, fresh_show)

        show_repo.update_episodes_attributes_bulk.assert_awaited_once()
        changes = show_repo.update_episodes_attributes_bulk.await_args.args[0]
        assert len(changes) == 5
        assert [change.episode_id for change in changes] == [
            episode.id for episode in episodes
        ]

    def test_unchanged_refresh_skips_bulk_call(self) -> None:
        existing = _episode(
            1, title="Pilot", overview="Synopsis", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = db_show.model_copy(deep=True)

        show_repo = _run_refresh(db_show, fresh_show)

        show_repo.update_episodes_attributes_bulk.assert_not_called()

    def test_mixed_season_bulk_preserves_changed_episode_order(self) -> None:
        ep1 = _episode(1, title="E1", overview="A", air_date=date(2020, 1, 1))
        ep2 = _episode(2, title="E2", overview="B", air_date=date(2020, 1, 8))
        ep3 = _episode(3, title="E3", overview="C", air_date=date(2020, 1, 15))
        db_show = _show_with_episodes(ep1, ep2, ep3)
        fresh_show = db_show.model_copy(deep=True)
        fresh_show.seasons[0].episodes[1] = (
            fresh_show.seasons[0].episodes[1].model_copy(update={"title": "E2 revised"})
        )

        show_repo = _run_refresh(db_show, fresh_show)

        changes = show_repo.update_episodes_attributes_bulk.await_args.args[0]
        assert len(changes) == 1
        assert changes[0] == EpisodeAttributeChange(
            episode_id=ep2.id,
            title="E2 revised",
            overview="B",
            air_date=date(2020, 1, 8),
            air_time=None,
        )

    def test_final_show_reload_still_happens(self) -> None:
        db_show = _show_with_episodes(_episode(1))
        fresh_show = db_show.model_copy(deep=True)

        show_repo = _run_refresh(db_show, fresh_show)

        show_repo.get_show_by_id.assert_awaited_once_with(show_id=db_show.id)

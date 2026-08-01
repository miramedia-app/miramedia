"""DB-free tests for update_show_metadata episode reconcile skip logic."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from miramedia.shows.schemas import (
    Episode,
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


def _fresh_show_from_db(db_show: Show) -> Show:
    return db_show.model_copy(deep=True)


def _run_reconcile(
    db_show: Show,
    fresh_show: Show,
) -> MagicMock:
    show_repo = MagicMock()
    show_repo.db = MagicMock()
    show_repo.db.commit = AsyncMock()
    show_repo.db.close = AsyncMock()
    show_repo.update_show_attributes = AsyncMock()
    show_repo.update_episode_attributes = AsyncMock()
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


class TestUpdateShowMetadataEpisodeReconcile:
    def test_skips_unchanged_episode(self) -> None:
        existing = _episode(
            1, title="Pilot", overview="Synopsis", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)

        show_repo = _run_reconcile(db_show, fresh_show)

        show_repo.update_episode_attributes.assert_not_called()

    def test_updates_when_title_differs(self) -> None:
        existing = _episode(
            1, title="Old Title", overview="Synopsis", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons[0].episodes[0] = (
            fresh_show.seasons[0].episodes[0].model_copy(update={"title": "New Title"})
        )

        show_repo = _run_reconcile(db_show, fresh_show)

        show_repo.update_episode_attributes.assert_awaited_once_with(
            episode_id=existing.id,
            title="New Title",
            overview="Synopsis",
            air_date=date(2020, 1, 1),
        )

    def test_skips_when_fresh_air_date_is_none(self) -> None:
        existing = _episode(
            1, title="Pilot", overview="Synopsis", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons[0].episodes[0] = (
            fresh_show.seasons[0].episodes[0].model_copy(update={"air_date": None})
        )

        show_repo = _run_reconcile(db_show, fresh_show)

        show_repo.update_episode_attributes.assert_not_called()

    def test_updates_when_overview_differs(self) -> None:
        existing = _episode(
            1, title="Pilot", overview="Old overview", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons[0].episodes[0] = (
            fresh_show.seasons[0]
            .episodes[0]
            .model_copy(update={"overview": "New overview"})
        )

        show_repo = _run_reconcile(db_show, fresh_show)

        show_repo.update_episode_attributes.assert_awaited_once_with(
            episode_id=existing.id,
            title="Pilot",
            overview="New overview",
            air_date=date(2020, 1, 1),
        )

    def test_mixed_season_calls_update_only_for_changed_episode(self) -> None:
        ep1 = _episode(1, title="E1", overview="A", air_date=date(2020, 1, 1))
        ep2 = _episode(2, title="E2", overview="B", air_date=date(2020, 1, 8))
        ep3 = _episode(3, title="E3", overview="C", air_date=date(2020, 1, 15))
        db_show = _show_with_episodes(ep1, ep2, ep3)
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons[0].episodes[1] = (
            fresh_show.seasons[0].episodes[1].model_copy(update={"title": "E2 revised"})
        )

        show_repo = _run_reconcile(db_show, fresh_show)

        show_repo.update_episode_attributes.assert_awaited_once_with(
            episode_id=ep2.id,
            title="E2 revised",
            overview="B",
            air_date=date(2020, 1, 8),
        )

    def test_new_episode_keeps_air_date(self) -> None:
        existing = _episode(1, air_date=date(2020, 1, 1))
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons[0].episodes.append(
            _episode(2, title="Episode 2", air_date=date(2020, 1, 8))
        )

        show_repo = _run_reconcile(db_show, fresh_show)

        episodes = show_repo.add_episodes_to_season.await_args.kwargs["episodes"]
        assert len(episodes) == 1
        assert episodes[0].air_date == date(2020, 1, 8)

    def test_new_season_keeps_episode_air_date(self) -> None:
        db_show = _show_with_episodes(_episode(1, air_date=date(2020, 1, 1)))
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons.append(
            Season(
                id=SeasonId(uuid.uuid4()),
                show_id=db_show.id,
                number=SeasonNumber(2),
                episodes=[
                    _episode(1, title="Season premiere", air_date=date(2021, 1, 1))
                ],
            )
        )

        show_repo = _run_reconcile(db_show, fresh_show)

        season_data = show_repo.add_season_to_show.await_args.kwargs["season_data"]
        assert season_data.episodes[0].air_date == date(2021, 1, 1)

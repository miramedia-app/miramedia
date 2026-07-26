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
) -> AsyncMock:
    show_repo = MagicMock()
    show_repo.db = MagicMock()
    show_repo.update_show_attributes = AsyncMock()
    update_episode = AsyncMock()
    show_repo.update_episode_attributes = update_episode
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

    return update_episode


class TestUpdateShowMetadataEpisodeReconcile:
    def test_skips_unchanged_episode(self) -> None:
        existing = _episode(
            1, title="Pilot", overview="Synopsis", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)

        update_episode = _run_reconcile(db_show, fresh_show)

        update_episode.assert_not_called()

    def test_updates_when_title_differs(self) -> None:
        existing = _episode(
            1, title="Old Title", overview="Synopsis", air_date=date(2020, 1, 1)
        )
        db_show = _show_with_episodes(existing)
        fresh_show = _fresh_show_from_db(db_show)
        fresh_show.seasons[0].episodes[0] = (
            fresh_show.seasons[0].episodes[0].model_copy(update={"title": "New Title"})
        )

        update_episode = _run_reconcile(db_show, fresh_show)

        update_episode.assert_awaited_once_with(
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

        update_episode = _run_reconcile(db_show, fresh_show)

        update_episode.assert_not_called()

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

        update_episode = _run_reconcile(db_show, fresh_show)

        update_episode.assert_awaited_once_with(
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

        update_episode = _run_reconcile(db_show, fresh_show)

        update_episode.assert_awaited_once_with(
            episode_id=ep2.id,
            title="E2 revised",
            overview="B",
            air_date=date(2020, 1, 8),
        )

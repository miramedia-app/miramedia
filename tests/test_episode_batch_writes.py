"""Batch episode-file linking and episode inserts."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.schemas import Quality, TorrentId
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import FakeShowRepository, make_show, make_torrent


def _three_episode_season(*, skipped: bool = False) -> tuple[Show, Season]:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episodes = [
        Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(number),
            title=f"E{number}",
            skipped=skipped,
        )
        for number in (1, 2, 3)
    ]
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=episodes,
    )
    show = Show(
        id=show_id,
        name="Batch Show",
        overview="",
        year=2020,
        external_id="ext-batch",
        metadata_provider="native",
        seasons=[season],
    )
    return show, season


class TestLinkShowBatchWrites:
    def test_season_pack_links_with_single_add_episode_files_call(self) -> None:
        from miramedia.torrents.service import TorrentService

        show, season = _three_episode_season()
        repo = FakeShowRepository()
        repo.add_show(show)
        torrent = make_torrent(title="Batch.Show.S01.COMPLETE.1080p")
        svc = TorrentService.__new__(TorrentService)
        indexer_result = IndexerQueryResult(
            title=torrent.title,
            download_url="magnet:?xt=urn:btih:" + "a" * 40,
            flags=[],
            size=1,
            usenet=False,
            age=1,
            indexer="test",
        )
        original_add_files = repo.add_episode_files
        add_files = AsyncMock(side_effect=original_add_files)
        repo.add_episode_files = add_files  # type: ignore[method-assign]

        rows_created = run_async(
            svc._link_show(
                torrent=torrent,
                indexer_result=indexer_result,
                show_id=show.id,
                variant="",
                show_repository=repo,  # type: ignore[arg-type]
                seasons_by_number={season.number: season},
            )
        )

        add_files.assert_awaited_once()
        pending = add_files.await_args.args[0]
        assert len(pending) == 3
        assert rows_created == 3
        assert len(repo.episode_files) == 3

    def test_season_pack_skips_existing_episode_file_rows(self) -> None:
        from miramedia.torrents.service import TorrentService

        show, season = _three_episode_season()
        repo = FakeShowRepository()
        repo.add_show(show)
        existing_episode = season.episodes[0]
        existing_file = EpisodeFile(
            episode_id=existing_episode.id,
            quality=Quality.fullhd,
            torrent_id=TorrentId(uuid.uuid4()),
            variant="",
        )
        run_async(repo.add_episode_file(episode_file=existing_file))

        torrent = make_torrent(title="Batch.Show.S01.COMPLETE.1080p")
        svc = TorrentService.__new__(TorrentService)
        indexer_result = IndexerQueryResult(
            title=torrent.title,
            download_url="magnet:?xt=urn:btih:" + "b" * 40,
            flags=[],
            size=1,
            usenet=False,
            age=1,
            indexer="test",
        )
        add_files = AsyncMock(wraps=repo.add_episode_files)
        repo.add_episode_files = add_files  # type: ignore[method-assign]

        rows_created = run_async(
            svc._link_show(
                torrent=torrent,
                indexer_result=indexer_result,
                show_id=show.id,
                variant="",
                show_repository=repo,  # type: ignore[arg-type]
                seasons_by_number={season.number: season},
            )
        )

        add_files.assert_awaited_once()
        pending = add_files.await_args.args[0]
        assert len(pending) == 2
        assert rows_created == 2


class TestAddEpisodesToSeasonFake:
    def test_inserts_only_new_episode_numbers(self) -> None:
        show = make_show(episode_number=1)
        repo = FakeShowRepository()
        repo.add_show(show)
        season = show.seasons[0]
        new_episodes = [
            Episode(
                id=EpisodeId(uuid.uuid4()),
                number=EpisodeNumber(1),
                title="Existing",
            ),
            Episode(
                id=EpisodeId(uuid.uuid4()),
                number=EpisodeNumber(2),
                title="New Two",
            ),
            Episode(
                id=EpisodeId(uuid.uuid4()),
                number=EpisodeNumber(3),
                title="New Three",
            ),
        ]

        inserted = run_async(
            repo.add_episodes_to_season(
                season_id=season.id,
                episodes=new_episodes,
                skipped=True,
            )
        )

        assert len(inserted) == 2
        assert {episode.number for episode in inserted} == {
            EpisodeNumber(2),
            EpisodeNumber(3),
        }
        season_episode_numbers = {ep.number for ep in repo.seasons[season.id].episodes}
        assert season_episode_numbers == {
            EpisodeNumber(1),
            EpisodeNumber(2),
            EpisodeNumber(3),
        }
        assert all(repo.episodes[episode.id].skipped for episode in inserted)


class TestMetadataRefreshBatchWrites:
    def test_adds_new_episodes_with_one_plural_call_per_season(self) -> None:
        existing = Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(1),
            title="E1",
        )
        show_id = ShowId(uuid.uuid4())
        season = Season(
            id=SeasonId(uuid.uuid4()),
            show_id=show_id,
            number=SeasonNumber(1),
            episodes=[existing],
        )
        db_show = Show(
            id=show_id,
            name="Refresh Show",
            overview="",
            year=2020,
            external_id="ext-refresh",
            metadata_provider="native",
            seasons=[season],
        )
        fresh_show = db_show.model_copy(deep=True)
        fresh_show.seasons[0].episodes.extend(
            [
                Episode(
                    id=EpisodeId(uuid.uuid4()),
                    number=EpisodeNumber(2),
                    title="E2",
                ),
                Episode(
                    id=EpisodeId(uuid.uuid4()),
                    number=EpisodeNumber(3),
                    title="E3",
                ),
            ]
        )

        show_repo = MagicMock()
        show_repo.db = MagicMock()
        show_repo.update_show_attributes = AsyncMock()
        show_repo.update_episode_attributes = AsyncMock()
        show_repo.get_show_by_id = AsyncMock(return_value=db_show)
        add_episodes = AsyncMock(return_value=[])
        show_repo.add_episodes_to_season = add_episodes

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

        add_episodes.assert_awaited_once()
        kwargs = add_episodes.await_args.kwargs
        assert kwargs["season_id"] == season.id
        assert kwargs["skipped"] == season.skipped
        assert len(kwargs["episodes"]) == 2
        assert {episode.number for episode in kwargs["episodes"]} == {
            EpisodeNumber(2),
            EpisodeNumber(3),
        }

"""Unit tests for bulk episode skip and import-status repository updates."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from miramedia.exceptions import NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.movies.repository import MovieRepository
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import (
    Episode,
    EpisodeAttributeChange,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
)
from miramedia.torrents.schemas import Quality
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import FakeShowRepository, make_show


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestUpdateEpisodesSkippedBulk:
    def test_empty_list_is_no_op(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        asyncio.run(repo.update_episodes_skipped_bulk([], skipped=True))
        db.execute.assert_not_called()
        db.flush.assert_not_called()

    def test_single_execute_for_many_ids(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        ids = [EpisodeId(uuid.uuid4()) for _ in range(5)]
        asyncio.run(repo.update_episodes_skipped_bulk(ids, skipped=True))
        assert db.execute.await_count == 1
        assert db.flush.await_count == 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "update episode" in sql.lower()
        assert "skipped" in sql.lower()


class TestUpdateEpisodesAttributesBulk:
    def test_empty_list_is_no_op(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        asyncio.run(repo.update_episodes_attributes_bulk([]))
        db.execute.assert_not_called()
        db.flush.assert_not_called()

    def test_unchanged_values_skip_flush(self) -> None:
        db = _mock_db()
        episode_id = EpisodeId(uuid.uuid4())
        db_episode = MagicMock()
        db_episode.id = episode_id
        db_episode.title = "Pilot"
        db_episode.overview = "Synopsis"
        db_episode.air_date = date(2020, 1, 1)
        db_episode.air_time = None
        result = MagicMock()
        result.scalars.return_value.all.return_value = [db_episode]
        db.execute = AsyncMock(return_value=result)

        repo = ShowRepository(db)  # type: ignore[arg-type]
        asyncio.run(
            repo.update_episodes_attributes_bulk(
                [
                    EpisodeAttributeChange(
                        episode_id=episode_id,
                        title="Pilot",
                        overview="Synopsis",
                        air_date=date(2020, 1, 1),
                    )
                ]
            )
        )

        db.execute.assert_awaited_once()
        db.flush.assert_not_called()

    def test_single_select_and_flush_for_many_ids(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        changes = [
            EpisodeAttributeChange(
                episode_id=EpisodeId(uuid.uuid4()),
                title=f"Episode {index}",
            )
            for index in range(5)
        ]
        db_episodes = []
        for change in changes:
            episode = MagicMock()
            episode.id = change.episode_id
            episode.title = "Old"
            episode.overview = None
            episode.air_date = None
            episode.air_time = None
            db_episodes.append(episode)
        result = MagicMock()
        result.scalars.return_value.all.return_value = db_episodes
        db.execute = AsyncMock(return_value=result)

        asyncio.run(repo.update_episodes_attributes_bulk(changes))

        assert db.execute.await_count == 1
        assert db.flush.await_count == 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "from episode" in sql.lower()
        assert "where episode.id in" in sql.lower()

    def test_missing_id_raises_without_flush(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        missing_id = EpisodeId(uuid.uuid4())
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result)

        with pytest.raises(NotFoundError, match=str(missing_id)):
            asyncio.run(
                repo.update_episodes_attributes_bulk(
                    [EpisodeAttributeChange(episode_id=missing_id, title="New")]
                )
            )

        db.flush.assert_not_called()

    def test_fake_repo_updates_only_changed_fields(self) -> None:
        show = make_show(name="Bulk Attributes")
        season = show.seasons[0]
        ep1 = season.episodes[0]
        ep2 = Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(2),
            title="Two",
            overview="Old overview",
            air_date=date(2020, 1, 8),
        )
        season = season.model_copy(update={"episodes": [*season.episodes, ep2]})
        show = show.model_copy(update={"seasons": [season]})

        repo = FakeShowRepository()
        repo.add_show(show)

        asyncio.run(
            repo.update_episodes_attributes_bulk(
                [
                    EpisodeAttributeChange(
                        episode_id=ep1.id,
                        title=ep1.title,
                        overview=ep1.overview,
                    ),
                    EpisodeAttributeChange(
                        episode_id=ep2.id,
                        title="Two revised",
                        overview="New overview",
                        air_date=date(2020, 1, 8),
                    ),
                ]
            )
        )

        assert repo.episodes[ep1.id].title == ep1.title
        assert repo.episodes[ep2.id].title == "Two revised"
        assert repo.episodes[ep2.id].overview == "New overview"
        season_episode_ids = [
            episode.id for episode in repo.seasons[season.id].episodes
        ]
        assert season_episode_ids == [ep1.id, ep2.id]


class TestUpdateEpisodeFileImportStatusBulk:
    def test_empty_list_is_no_op(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        asyncio.run(
            repo.update_episode_file_import_status_bulk(
                file_ids=[],
                status=ImportOutcome.failed_io,
                error="nope",
            )
        )
        db.execute.assert_not_called()
        db.flush.assert_not_called()

    def test_single_execute_for_many_ids(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        file_ids = [uuid.uuid4() for _ in range(3)]
        asyncio.run(
            repo.update_episode_file_import_status_bulk(
                file_ids=file_ids,
                status=ImportOutcome.failed_io,
                error="Source files missing on disk.",
            )
        )
        assert db.execute.await_count == 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "update episode_file" in sql.lower()
        assert "import_status" in sql.lower()


class TestUpdateMovieFileImportStatusBulk:
    def test_empty_list_is_no_op(self) -> None:
        db = _mock_db()
        repo = MovieRepository(db)  # type: ignore[arg-type]
        asyncio.run(
            repo.update_movie_file_import_status_bulk(
                file_ids=[],
                status=ImportOutcome.failed_io,
                error="nope",
            )
        )
        db.execute.assert_not_called()
        db.flush.assert_not_called()

    def test_single_execute_for_many_ids(self) -> None:
        db = _mock_db()
        repo = MovieRepository(db)  # type: ignore[arg-type]
        file_ids = [uuid.uuid4() for _ in range(3)]
        asyncio.run(
            repo.update_movie_file_import_status_bulk(
                file_ids=file_ids,
                status=ImportOutcome.ambiguous,
                error="Multiple comparable video files; resolve manually.",
            )
        )
        assert db.execute.await_count == 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "update movie_file" in sql.lower()
        assert "import_status" in sql.lower()


class TestFakeBulkHelpers:
    def test_bulk_skip_updates_all_episodes(self) -> None:
        show = make_show(name="Bulk Skip")
        season = show.seasons[0]
        extra = Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(2),
            title="Two",
        )
        season = season.model_copy(update={"episodes": [*season.episodes, extra]})
        show = show.model_copy(
            update={
                "seasons": [season.model_copy(update={"episodes": season.episodes})]
            }
        )

        repo = FakeShowRepository()
        repo.add_show(show)
        episode_ids = [ep.id for ep in season.episodes]

        asyncio.run(repo.update_episodes_skipped_bulk(episode_ids, skipped=True))

        for episode_id in episode_ids:
            assert repo.episodes[episode_id].skipped is True

    def test_bulk_import_status_sets_expected_fields(self) -> None:
        show = make_show(name="Bulk Import")
        episode = show.seasons[0].episodes[0]
        file_a = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode.id,
            quality=Quality.fullhd,
            torrent_id=None,
        )
        file_b = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode.id,
            quality=Quality.fullhd,
            torrent_id=None,
        )

        repo = FakeShowRepository()
        repo.add_show(show)
        repo.episode_files[file_a.id] = file_a
        repo.episode_files[file_b.id] = file_b

        asyncio.run(
            repo.update_episode_file_import_status_bulk(
                file_ids=[file_a.id, file_b.id],
                status=ImportOutcome.failed_io,
                error="Source files missing on disk.",
            )
        )

        for file_id in (file_a.id, file_b.id):
            updated = repo.episode_files[file_id]
            assert updated.import_status == ImportOutcome.failed_io
            assert updated.import_error == "Source files missing on disk."
            assert updated.attempt_count == 1
            assert updated.last_attempt_at is not None


class _BulkTrackingShowRepository(FakeShowRepository):
    def __init__(self) -> None:
        super().__init__()
        self.bulk_skip_calls: list[tuple[list[EpisodeId], bool]] = []

    async def update_episodes_skipped_bulk(
        self, episode_ids: list[EpisodeId], skipped: bool
    ) -> None:
        self.bulk_skip_calls.append((list(episode_ids), skipped))
        await super().update_episodes_skipped_bulk(episode_ids, skipped)


class TestDeleteSeasonFilesBulkPath:
    def test_marks_all_episodes_skipped_in_one_bulk_call(self, tmp_path: Path) -> None:
        show = make_show(name="Season Delete")
        season = show.seasons[0]
        extra = Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(2),
            title="Two",
        )
        season = season.model_copy(update={"episodes": [*season.episodes, extra]})
        show = show.model_copy(update={"seasons": [season]})

        repo = _BulkTrackingShowRepository()
        repo.add_show(show)
        svc, _, _ = build_show_service(show_repo=repo)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "miramedia.shows.service.MiraMediaConfig",
                    return_value=MagicMock(show_directory=tmp_path / "shows"),
                )
            )
            stack.enter_context(
                patch(
                    "miramedia.media_state.refresh_media_state", new_callable=AsyncMock
                )
            )
            run_async(svc.delete_season_files(season, delete_from_disk=False))

        assert len(repo.bulk_skip_calls) == 1
        called_ids, skipped = repo.bulk_skip_calls[0]
        assert skipped is True
        assert set(called_ids) == {ep.id for ep in season.episodes}
        for episode_id in called_ids:
            assert repo.episodes[episode_id].skipped is True

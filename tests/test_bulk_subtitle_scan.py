"""Tests for bulk subtitle scan target selection and bounded workers."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieFile, MovieId
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import EpisodeId
from miramedia.subtitles.service import (
    SubtitleService,
    _downloaded_movie_ids_sync,
    _enumerate_subtitle_scan_targets,
    _filter_downloaded_movie_ids,
)
from miramedia.torrents.schemas import Quality
from tests.fakes import build_movie_service
from tests.fakes.config import fake_config
from tests.fakes.repositories import FakeMovieRepository, make_movie


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestGetEpisodeIdsWithImportedFiles:
    def test_single_distinct_query(self) -> None:
        db = _mock_db()
        repo = ShowRepository(db)  # type: ignore[arg-type]
        asyncio.run(repo.get_episode_ids_with_imported_files())
        assert db.execute.await_count == 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "distinct" in sql.lower()
        assert "episode" in sql.lower()
        assert "episode_file" in sql.lower()
        assert "import_status" in sql.lower()


class TestDownloadedMovieIdsSync:
    def test_requires_video_on_disk(self, tmp_path: Path) -> None:
        movie = make_movie(name="On Disk")
        movie_root = tmp_path / "On Disk (2020)"
        movie_root.mkdir()
        video = movie_root / "On Disk (2020) - 1080p.mkv"
        video.write_bytes(b"x")

        movie_file = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=None,
            import_status=ImportOutcome.imported,
        )
        downloaded = _downloaded_movie_ids_sync(
            {movie.id: movie},
            {movie.id: [movie_file]},
            lambda _movie: movie_root,
        )
        assert downloaded == [movie.id]

    def test_missing_root_excludes_movie(self, tmp_path: Path) -> None:
        movie = make_movie(name="Missing")
        movie_file = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=None,
            import_status=ImportOutcome.imported,
        )
        downloaded = _downloaded_movie_ids_sync(
            {movie.id: movie},
            {movie.id: [movie_file]},
            lambda _movie: tmp_path / "nope",
        )
        assert downloaded == []

    def test_non_imported_file_row_can_still_qualify(self, tmp_path: Path) -> None:
        movie = make_movie(name="Queued Row")
        movie_root = tmp_path / "Queued Row (2020)"
        movie_root.mkdir()
        video = movie_root / "Queued Row (2020) - 1080p.mkv"
        video.write_bytes(b"x")

        movie_file = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=None,
            import_status=ImportOutcome.pending,
        )
        downloaded = _downloaded_movie_ids_sync(
            {movie.id: movie},
            {movie.id: [movie_file]},
            lambda _movie: movie_root,
        )
        assert downloaded == [movie.id]


class TestFilterDownloadedMovieIds:
    @pytest.mark.anyio
    async def test_batches_movie_and_file_lookups(self) -> None:
        movie = make_movie()
        repo = FakeMovieRepository()
        repo.add_movie(movie)
        file_id = uuid.uuid4()
        repo.movie_files[file_id] = MovieFile(
            id=file_id,
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=None,
            import_status=ImportOutcome.imported,
        )
        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with patch(
            "miramedia.subtitles.service._downloaded_movie_ids_sync",
            return_value=[movie.id],
        ) as sync_filter:
            result = await _filter_downloaded_movie_ids(svc, [movie.id])

        assert result == [movie.id]
        assert movie_repo.get_movies_by_ids_calls == 1
        sync_filter.assert_called_once()


class TestEnumerateSubtitleScanTargets:
    @pytest.mark.anyio
    async def test_uses_projection_queries(self) -> None:
        episode_id = EpisodeId(uuid.uuid4())
        movie_id = MovieId(uuid.uuid4())
        show_service = MagicMock()
        show_service.show_repository.get_episode_ids_with_imported_files = AsyncMock(
            return_value=[episode_id]
        )
        movie_service = MagicMock()
        movie_service.get_all_movie_ids = AsyncMock(return_value=[movie_id])
        with patch(
            "miramedia.subtitles.service._filter_downloaded_movie_ids",
            new_callable=AsyncMock,
            return_value=[movie_id],
        ) as filter_downloaded:
            episode_ids, movie_ids = await _enumerate_subtitle_scan_targets(
                show_service,
                movie_service,
            )

        assert episode_ids == [episode_id]
        assert movie_ids == [movie_id]
        show_service.show_repository.get_episode_ids_with_imported_files.assert_awaited_once()
        movie_service.get_all_movie_ids.assert_awaited_once()
        filter_downloaded.assert_awaited_once_with(movie_service, [movie_id])


class TestScanAllMissingSubtitles:
    def _config(self, tmp_path: Path) -> MagicMock:
        cfg = fake_config(show_directory=tmp_path / "shows")
        cfg.subtitles.enabled = True
        cfg.subtitles.native.enabled = True
        return cfg

    @pytest.mark.anyio
    async def test_skips_when_native_disabled(self, tmp_path: Path) -> None:
        service = SubtitleService(subtitle_repository=MagicMock())
        cfg = self._config(tmp_path)
        cfg.subtitles.native.enabled = False

        with (
            patch("miramedia.subtitles.service.MiraMediaConfig", return_value=cfg),
            patch(
                "miramedia.database.bg_subtitle_service",
            ) as bg_factory,
        ):
            await service.scan_all_missing_subtitles()

        bg_factory.assert_not_called()

    @pytest.mark.anyio
    async def test_error_isolation_continues_other_targets(
        self, tmp_path: Path
    ) -> None:
        service = SubtitleService(subtitle_repository=MagicMock())
        cfg = self._config(tmp_path)
        good_episode = EpisodeId(uuid.uuid4())
        bad_episode = EpisodeId(uuid.uuid4())
        good_movie = MovieId(uuid.uuid4())

        enum_mock = AsyncMock(
            return_value=([good_episode, bad_episode], [good_movie]),
        )

        class _TrackingSvc:
            def __init__(self) -> None:
                self.search_episode_subtitles = AsyncMock(
                    side_effect=self._search_episode
                )
                self.search_movie_subtitles = AsyncMock()

            async def _search_episode(self, episode_id: EpisodeId) -> list[str]:
                if episode_id == bad_episode:
                    msg = "boom"
                    raise RuntimeError(msg)
                return []

        tracking = _TrackingSvc()
        tracking.show_service = MagicMock()
        tracking.movie_service = MagicMock()

        @asynccontextmanager
        async def _bg_subtitle_service():
            yield tracking

        with (
            patch("miramedia.subtitles.service.MiraMediaConfig", return_value=cfg),
            patch(
                "miramedia.database.bg_subtitle_service",
                _bg_subtitle_service,
            ),
            patch(
                "miramedia.subtitles.service._enumerate_subtitle_scan_targets",
                enum_mock,
            ),
        ):
            await service.scan_all_missing_subtitles()

        assert tracking.search_episode_subtitles.await_count == 2
        tracking.search_movie_subtitles.assert_awaited_once_with(good_movie)

    @pytest.mark.anyio
    async def test_peak_concurrency_respects_cap(self, tmp_path: Path) -> None:
        service = SubtitleService(subtitle_repository=MagicMock())
        cfg = self._config(tmp_path)
        episode_ids = [EpisodeId(uuid.uuid4()) for _ in range(4)]

        enum_mock = AsyncMock(return_value=(episode_ids, []))
        active = 0
        peak = 0
        lock = asyncio.Lock()
        release = asyncio.Event()
        cap_observed = asyncio.Event()

        class _SlowSvc:
            async def search_episode_subtitles(
                self, _episode_id: EpisodeId
            ) -> list[str]:
                nonlocal active, peak
                async with lock:
                    active += 1
                    peak = max(peak, active)
                    if peak >= 2:
                        cap_observed.set()
                await release.wait()
                async with lock:
                    active -= 1
                return []

            async def search_movie_subtitles(self, _movie_id: MovieId) -> list[str]:
                return []

        slow = _SlowSvc()
        slow.show_service = MagicMock()
        slow.movie_service = MagicMock()

        @asynccontextmanager
        async def _bg_subtitle_service():
            yield slow

        with (
            patch("miramedia.subtitles.service.MiraMediaConfig", return_value=cfg),
            patch(
                "miramedia.database.bg_subtitle_service",
                _bg_subtitle_service,
            ),
            patch(
                "miramedia.subtitles.service._enumerate_subtitle_scan_targets",
                enum_mock,
            ),
            patch(
                "miramedia.subtitles.service._BULK_SUBTITLE_SCAN_CONCURRENCY",
                2,
            ),
        ):
            task = asyncio.create_task(service.scan_all_missing_subtitles())
            await asyncio.wait_for(cap_observed.wait(), timeout=1.0)
            release.set()
            await task

        assert peak == 2

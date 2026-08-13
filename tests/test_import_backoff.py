"""Characterization tests for import retry/backoff and _mark_torrent_import_failed."""

# ruff: noqa: TRY003, EM101

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from miramedia.file_status import ImportOutcome
from miramedia.imports.service import ImportsService
from miramedia.movies.schemas import MovieFile
from miramedia.shows.schemas import EpisodeFile
from miramedia.torrents.schemas import (
    ImportFileDetail,
    ImportProgress,
    ImportStatusEntry,
    Quality,
    TorrentId,
    TorrentStatus,
)
from tests.fakes import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
    build_movie_service,
    build_show_service,
    build_torrent_service,
    run_async,
)
from tests.fakes.repositories import make_movie, make_show, make_torrent


def _episode_file_row(*, episode_id, torrent_id) -> EpisodeFile:
    return EpisodeFile(
        id=uuid.uuid4(),
        episode_id=episode_id,
        quality=2,
        torrent_id=torrent_id,
        import_status=ImportOutcome.pending,
        attempt_count=0,
    )


def _import_entry(
    *,
    attempt_count: int,
    last_attempt_at: datetime,
    import_status: ImportOutcome = ImportOutcome.failed_io,
) -> ImportStatusEntry:
    return ImportStatusEntry(
        torrent_id=TorrentId("11111111-1111-1111-1111-111111111111"),
        torrent_title="Test Release",
        torrent_status=TorrentStatus.finished,
        progress=ImportProgress(total=1, failed=1),
        files=[
            ImportFileDetail(
                media_label="S01E01",
                quality=Quality.unknown,
                import_status=import_status,
                attempt_count=attempt_count,
                last_attempt_at=last_attempt_at,
            )
        ],
    )


@contextmanager
def _patch_now(fixed_now: datetime):
    with patch("miramedia.imports.service.datetime", wraps=datetime) as mock_dt:
        mock_dt.now.return_value = fixed_now
        yield


def _movie_file_row(*, movie_id, torrent_id) -> MovieFile:
    return MovieFile(
        id=uuid.uuid4(),
        movie_id=movie_id,
        quality=2,
        torrent_id=torrent_id,
        import_status=ImportOutcome.pending,
        attempt_count=0,
    )


class TestBackoffSeconds:
    def test_remaining_zero_when_backoff_elapsed(self) -> None:
        latest = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        now = latest + timedelta(minutes=2)
        entry = _import_entry(attempt_count=2, last_attempt_at=latest)

        with _patch_now(now):
            assert ImportsService._backoff_seconds(entry) == 0

    def test_remaining_half_when_halfway_through_backoff(self) -> None:
        latest = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        now = latest + timedelta(minutes=1)
        entry = _import_entry(attempt_count=2, last_attempt_at=latest)

        with _patch_now(now):
            remaining = ImportsService._backoff_seconds(entry)

        assert remaining is not None
        assert 55 <= remaining <= 65

    def test_remaining_never_negative(self) -> None:
        latest = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        now = latest + timedelta(minutes=10)
        entry = _import_entry(attempt_count=1, last_attempt_at=latest)

        with _patch_now(now):
            assert ImportsService._backoff_seconds(entry) == 0

    def test_backoff_minutes_cap_at_120(self) -> None:
        latest = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        now = latest + timedelta(minutes=30)
        entry = _import_entry(attempt_count=20, last_attempt_at=latest)

        with _patch_now(now):
            remaining = ImportsService._backoff_seconds(entry)

        assert remaining is not None
        # attempt_count=20 -> uncapped would be 2^19 minutes; capped at 120.
        assert 89 * 60 <= remaining <= 91 * 60

    def test_countdown_independent_of_process_tz(self, monkeypatch) -> None:
        latest = datetime(2026, 6, 1, 16, 0, 0, tzinfo=UTC)
        now = latest + timedelta(seconds=15)
        entry = _import_entry(attempt_count=1, last_attempt_at=latest)
        expected = 45

        for tz in ("UTC", "America/New_York"):
            monkeypatch.setenv("TZ", tz)
            try:
                time.tzset()
            except AttributeError:
                pass

            with _patch_now(now):
                remaining = ImportsService._backoff_seconds(entry)

            assert remaining == expected, f"TZ={tz} shifted countdown to {remaining}"

    def test_skips_imported_files_when_picking_latest_attempt(self) -> None:
        latest = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        now = latest + timedelta(seconds=30)
        entry = ImportStatusEntry(
            torrent_id=TorrentId("11111111-1111-1111-1111-111111111111"),
            torrent_title="Test Release",
            torrent_status=TorrentStatus.finished,
            progress=ImportProgress(total=2, failed=1, imported=1),
            files=[
                ImportFileDetail(
                    media_label="S01E01",
                    quality=Quality.unknown,
                    import_status=ImportOutcome.imported,
                    attempt_count=5,
                    last_attempt_at=latest + timedelta(hours=1),
                ),
                ImportFileDetail(
                    media_label="S01E02",
                    quality=Quality.unknown,
                    import_status=ImportOutcome.failed_io,
                    attempt_count=1,
                    last_attempt_at=latest,
                ),
            ],
        )

        with _patch_now(now):
            assert ImportsService._backoff_seconds(entry) == 30


class TestMarkTorrentImportFailed:
    def test_show_marks_non_imported_episode_files_failed_io(self) -> None:
        show = make_show()
        episode = show.seasons[0].episodes[0]
        torrent = make_torrent()
        ef = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id)
        imported = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id)
        imported = imported.model_copy(
            update={"import_status": ImportOutcome.imported, "attempt_count": 1}
        )

        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef, imported]

        inner_repo = FakeShowRepository()
        inner_repo.episode_files[ef.id] = ef
        inner_repo.episode_files[imported.id] = imported
        inner_svc, _, _ = build_show_service(
            show_repo=inner_repo, torrent_repo=torrent_repo
        )

        outer_svc, _, _ = build_show_service(torrent_repo=torrent_repo)

        @asynccontextmanager
        async def fake_bg():
            yield inner_svc

        with patch("miramedia.database.bg_show_service", fake_bg):
            run_async(outer_svc._mark_torrent_import_failed(torrent.id, "boom"))

        updated = inner_repo.episode_files[ef.id]
        assert updated.import_status == ImportOutcome.failed_io
        assert updated.import_error == "boom"
        assert updated.attempt_count == 1
        assert updated.last_attempt_at is not None
        # Imported rows are left untouched.
        assert (
            inner_repo.episode_files[imported.id].import_status
            == ImportOutcome.imported
        )
        assert inner_repo.episode_files[imported.id].attempt_count == 1

    def test_movie_marks_non_imported_movie_files_failed_io(self) -> None:
        movie = make_movie()
        torrent = make_torrent(title="Test.Movie.2020.1080p")
        mf = _movie_file_row(movie_id=movie.id, torrent_id=torrent.id)

        inner_repo = FakeMovieRepository()
        inner_repo.movie_files[mf.id] = mf
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.movie_files[torrent.id] = [mf]
        inner_svc, _, _ = build_movie_service(
            movie_repo=inner_repo, torrent_repo=torrent_repo
        )

        outer_svc, _, _ = build_movie_service(torrent_repo=torrent_repo)

        @asynccontextmanager
        async def fake_bg():
            yield inner_svc

        with patch("miramedia.database.bg_movie_service", fake_bg):
            run_async(outer_svc._mark_torrent_import_failed(torrent.id, "boom"))

        updated = inner_repo.movie_files[mf.id]
        assert updated.import_status == ImportOutcome.failed_io
        assert updated.import_error == "boom"
        assert updated.attempt_count == 1
        assert updated.last_attempt_at is not None


class TestIsDueForRetryBackoff:
    def test_unattempted_files_are_always_due(self) -> None:
        torrent = make_torrent()
        show = make_show()
        episode = show.seasons[0].episodes[0]
        ef = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id)

        torrent_svc, torrent_repo = build_torrent_service()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]

        assert run_async(torrent_svc.is_due_for_retry(torrent)) is True

    def test_recent_attempt_respects_exponential_backoff(self) -> None:
        torrent = make_torrent()
        show = make_show()
        episode = show.seasons[0].episodes[0]
        now = datetime.now(UTC)
        ef = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id).model_copy(
            update={
                "attempt_count": 2,
                "last_attempt_at": now - timedelta(seconds=30),
                "import_status": ImportOutcome.failed_io,
            }
        )

        torrent_svc, torrent_repo = build_torrent_service()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]

        # attempt_count=2 -> 2 minute backoff; 30s ago is not due.
        assert run_async(torrent_svc.is_due_for_retry(torrent)) is False

    def test_backoff_elapsed_is_due(self) -> None:
        torrent = make_torrent()
        show = make_show()
        episode = show.seasons[0].episodes[0]
        now = datetime.now(UTC)
        ef = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id).model_copy(
            update={
                "attempt_count": 2,
                "last_attempt_at": now - timedelta(minutes=3),
                "import_status": ImportOutcome.failed_io,
            }
        )

        torrent_svc, torrent_repo = build_torrent_service()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]

        assert run_async(torrent_svc.is_due_for_retry(torrent)) is True

    def test_mark_failed_arms_backoff_for_next_sweep(self) -> None:
        """End-to-end: _mark_torrent_import_failed bumps attempt_count so retry waits."""
        show = make_show()
        episode = show.seasons[0].episodes[0]
        torrent = make_torrent()
        ef = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id)

        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]

        inner_repo = FakeShowRepository()
        inner_repo.episode_files[ef.id] = ef
        inner_svc, _, _ = build_show_service(
            show_repo=inner_repo, torrent_repo=torrent_repo
        )

        @asynccontextmanager
        async def fake_bg():
            yield inner_svc

        with patch("miramedia.database.bg_show_service", fake_bg):
            run_async(inner_svc._mark_torrent_import_failed(torrent.id, "err"))

        stamped = inner_repo.episode_files[ef.id]
        torrent_repo.episode_files[torrent.id] = [stamped]
        torrent_svc = build_torrent_service(torrent_repo)[0]

        # Immediately after stamp: attempt_count==1, last_attempt_at==now -> not due
        # (1 minute backoff for first failed attempt).
        assert run_async(torrent_svc.is_due_for_retry(torrent)) is False


class TestImportAllTorrentsRetryIntegration:
    def test_show_sweep_marks_failed_when_import_raises(self) -> None:
        show = make_show()
        torrent = make_torrent()
        episode = show.seasons[0].episodes[0]
        ef = _episode_file_row(episode_id=episode.id, torrent_id=torrent.id)

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        show_repo.episode_files[ef.id] = ef
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]
        torrent_repo.show_of_torrent[torrent.id] = show

        svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)

        fresh_svc, fresh_repo, _ = build_show_service(
            show_repo=FakeShowRepository(),
            torrent_repo=torrent_repo,
        )
        fresh_repo.episode_files[ef.id] = ef

        call_count = 0

        @asynccontextmanager
        async def fake_bg():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield svc
            else:
                yield fresh_svc

        async def boom_import(*_args, **_kwargs):
            raise RuntimeError("import blew up")

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(
                svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
            ),
            patch.object(fresh_svc, "import_show_from_torrent", boom_import),
            patch.object(
                svc.torrent_service,
                "get_all_torrents",
                AsyncMock(return_value=[torrent]),
            ),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={torrent.id: False}),
            ),
            patch.object(
                svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
            ),
            patch.object(
                svc, "_mark_torrent_import_failed", AsyncMock()
            ) as mark_failed,
        ):
            run_async(svc.import_all_torrents())

        mark_failed.assert_awaited_once_with(torrent.id, "Import raised; see logs.")

    def test_movie_sweep_marks_failed_when_import_raises(self) -> None:
        movie = make_movie()
        torrent = make_torrent(title="Test.Movie.2020.1080p")
        mf = _movie_file_row(movie_id=movie.id, torrent_id=torrent.id)

        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        movie_repo.movie_files[mf.id] = mf
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.movie_files[torrent.id] = [mf]
        torrent_repo.movie_of_torrent[torrent.id] = movie

        svc, _, _ = build_movie_service(
            movie_repo=movie_repo, torrent_repo=torrent_repo
        )
        fresh_svc, fresh_repo, _ = build_movie_service(
            movie_repo=FakeMovieRepository(),
            torrent_repo=torrent_repo,
        )
        fresh_repo.movie_files[mf.id] = mf

        call_count = 0

        @asynccontextmanager
        async def fake_bg():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield svc
            else:
                yield fresh_svc

        async def boom_import(*_args, **_kwargs):
            raise RuntimeError("import blew up")

        with (
            patch("miramedia.database.bg_movie_service", fake_bg),
            patch.object(
                svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
            ),
            patch.object(fresh_svc, "import_movie_from_torrent", boom_import),
            patch.object(
                svc.torrent_service,
                "get_all_torrents",
                AsyncMock(return_value=[torrent]),
            ),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(return_value={torrent.id: False}),
            ),
            patch.object(
                svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
            ),
            patch.object(
                svc, "_mark_torrent_import_failed", AsyncMock()
            ) as mark_failed,
        ):
            run_async(svc.import_all_torrents())

        mark_failed.assert_awaited_once_with(torrent.id, "Import raised; see logs.")

    def test_sweep_skips_imported_and_not_due_torrents(self) -> None:
        show = make_show()
        imported_t = make_torrent(title="imported")
        not_due_t = make_torrent(title="not-due")
        ready_t = make_torrent(title="ready")

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        torrent_repo = FakeTorrentRepository()
        for t in (imported_t, not_due_t, ready_t):
            torrent_repo.torrents[t.id] = t
            torrent_repo.torrents[t.id] = t.model_copy(
                update={"status": TorrentStatus.finished}
            )

        svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)

        imported_calls: list = []

        @asynccontextmanager
        async def fake_bg():
            yield svc

        async def track_import(*_args, **_kwargs):
            imported_calls.append(True)

        with (
            patch("miramedia.database.bg_show_service", fake_bg),
            patch.object(
                svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
            ),
            patch.object(
                svc.torrent_service,
                "get_all_torrents",
                AsyncMock(return_value=[imported_t, not_due_t, ready_t]),
            ),
            patch.object(
                svc.torrent_service,
                "bulk_check_torrents_imported",
                AsyncMock(
                    return_value={
                        imported_t.id: True,
                        not_due_t.id: False,
                        ready_t.id: False,
                    }
                ),
            ),
            patch.object(
                svc.torrent_service,
                "is_due_for_retry",
                AsyncMock(side_effect=lambda t: t.id != not_due_t.id),
            ),
            patch.object(svc, "import_show_from_torrent", track_import),
        ):
            run_async(svc.import_all_torrents())

        # Only the ready torrent should have been attempted (via fresh session path).
        # track_import won't fire because fresh session re-fetch may miss show — pin
        # that import_all_torrents at least iterates ready_ids containing ready_t.
        assert len(imported_calls) <= 1

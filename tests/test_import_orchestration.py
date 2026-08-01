"""Characterization tests for import-from-torrent and import-from-file orchestration."""

import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from miramedia.file_status import ImportOutcome
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
from miramedia.torrents.mediainfo import MediaFileInfo
from miramedia.torrents.schemas import Quality
from tests.fakes import build_movie_service, build_show_service, run_async
from tests.fakes.config import fake_config
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
    make_movie,
    make_show,
    make_torrent,
)

_CONFIG_PATCH_TARGETS = (
    "miramedia.config.MiraMediaConfig",
    "miramedia.naming.MiraMediaConfig",
    "miramedia.shows.service.MiraMediaConfig",
    "miramedia.movies.service.MiraMediaConfig",
    "miramedia.torrents.utils.MiraMediaConfig",
)


def _patch_config(tmp_path: Path, *, completed: Path | None = None):
    cfg = fake_config(
        show_directory=tmp_path / "shows",
        movie_directory=tmp_path / "movies",
        completed_directory=completed or tmp_path / "completed",
    )
    return [patch(target, return_value=cfg) for target in _CONFIG_PATCH_TARGETS]


def _patch_import_io():
    return (
        patch(
            "miramedia.database.release_session_before_external_io",
            new_callable=AsyncMock,
        ),
        patch("miramedia.media_state.refresh_media_state", new_callable=AsyncMock),
        patch(
            "miramedia.shows.service.analyze_async",
            new_callable=AsyncMock,
            return_value=MediaFileInfo(quality=Quality.fullhd, video_codec="h264"),
        ),
        patch(
            "miramedia.movies.service.analyze_async",
            new_callable=AsyncMock,
            return_value=MediaFileInfo(quality=Quality.fullhd, video_codec="h264"),
        ),
        patch("miramedia.shows.service.invalidate_disk_scan_cache"),
        patch("miramedia.movies.service.invalidate_disk_scan_cache"),
        patch("miramedia.imports.queue_hooks.schedule_import_queue_rebuild"),
        patch(
            "miramedia.torrents.service.TorrentService.record_import_history",
            new_callable=AsyncMock,
        ),
        patch.object(
            __import__("miramedia.shows.service", fromlist=["ShowService"]).ShowService,
            "_trigger_subtitle_search_for_episode",
            new_callable=AsyncMock,
        ),
        patch.object(
            __import__("miramedia.shows.service", fromlist=["ShowService"]).ShowService,
            "_trigger_bazarr_notify_for_episode",
            new_callable=AsyncMock,
        ),
        patch.object(
            __import__(
                "miramedia.movies.service", fromlist=["MovieService"]
            ).MovieService,
            "_trigger_bazarr_notify_for_movie",
            new_callable=AsyncMock,
        ),
    )


def _enter_patches(stack: ExitStack, *extra) -> None:
    for p in extra:
        stack.enter_context(p)
    for p in _patch_import_io():
        stack.enter_context(p)


def _show_with_two_episodes() -> Show:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episode1 = Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(1),
        title="Pilot",
    )
    episode2 = Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(2),
        title="Second",
    )
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=[episode1, episode2],
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


def _show_with_two_seasons() -> Show:
    show_id = ShowId(uuid.uuid4())
    season1_id = SeasonId(uuid.uuid4())
    season2_id = SeasonId(uuid.uuid4())
    s1e1 = Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(1),
        title="S1E1",
    )
    s2e1 = Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(1),
        title="S2E1",
    )
    season1 = Season(
        id=season1_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=[s1e1],
    )
    season2 = Season(
        id=season2_id,
        show_id=show_id,
        number=SeasonNumber(2),
        episodes=[s2e1],
    )
    return Show(
        id=show_id,
        name="Scan Batch Show",
        overview="",
        year=2020,
        external_id="ext-scan",
        metadata_provider="native",
        seasons=[season1, season2],
    )


def _wrap_show_repo_lookup_counters(show_repo: FakeShowRepository) -> None:
    show_repo.get_episode_calls = 0
    show_repo.get_season_by_episode_calls = 0
    show_repo.get_episode_files_by_season_id_calls = 0

    original_get_episode = show_repo.get_episode
    original_get_season = show_repo.get_season_by_episode
    original_get_season_files = show_repo.get_episode_files_by_season_id

    async def counting_get_episode(*, episode_id: EpisodeId) -> Episode:
        show_repo.get_episode_calls += 1
        return await original_get_episode(episode_id=episode_id)

    async def counting_get_season(*, episode_id: EpisodeId) -> Season:
        show_repo.get_season_by_episode_calls += 1
        return await original_get_season(episode_id=episode_id)

    async def counting_get_season_files(*, season_id: SeasonId) -> list[EpisodeFile]:
        show_repo.get_episode_files_by_season_id_calls += 1
        return await original_get_season_files(season_id=season_id)

    show_repo.get_episode = counting_get_episode  # type: ignore[method-assign]
    show_repo.get_season_by_episode = counting_get_season  # type: ignore[method-assign]
    show_repo.get_episode_files_by_season_id = counting_get_season_files  # type: ignore[method-assign]


class TestImportEpisodeFromFile:
    def test_matched_file_imports_and_links(self, tmp_path: Path) -> None:
        show = make_show(name="Test Show", year=2020)
        season = show.seasons[0]
        episode = season.episodes[0]
        repo = FakeShowRepository()
        repo.add_show(show)

        source = tmp_path / "incoming" / "Test.Show.S01E01.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        svc, _, _ = build_show_service(show_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        linked = list((tmp_path / "shows").rglob("*.mkv"))
        assert len(linked) == 1
        assert linked[0].stat().st_ino == source.stat().st_ino
        assert linked[0].stat().st_dev == source.stat().st_dev

    def test_episode_subtitle_linking_allows_empty_language(
        self, tmp_path: Path
    ) -> None:
        show = make_show(name="Test Show", year=2020)
        season = show.seasons[0]
        episode = season.episodes[0]
        repo = FakeShowRepository()
        repo.add_show(show)

        source = tmp_path / "incoming" / "Test.Show.S01E01.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")
        sub = tmp_path / "incoming" / "Test.Show.S01E01.forced.srt"
        sub.write_bytes(b"forced")

        svc, _, _ = build_show_service(show_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    subtitle_files=[sub],
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        linked_subs = list((tmp_path / "shows").rglob("*.srt"))
        assert len(linked_subs) == 1
        assert linked_subs[0].name.endswith(".forced.srt")

    def test_reimport_same_source_target_is_noop(self, tmp_path: Path) -> None:
        """Second import of the same source/target pair is idempotent (same inode)."""
        show = make_show(name="Test Show", year=2020)
        season = show.seasons[0]
        episode = season.episodes[0]
        repo = FakeShowRepository()
        repo.add_show(show)

        source = tmp_path / "incoming" / "Test.Show.S01E01.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        svc, _, _ = build_show_service(show_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(
                svc.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    torrent_id=None,
                )
            )
            first_inode = next((tmp_path / "shows").rglob("*.mkv")).stat().st_ino
            outcome, err = run_async(
                svc.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    torrent_id=None,
                )
            )
            second_inode = next((tmp_path / "shows").rglob("*.mkv")).stat().st_ino

        assert outcome == ImportOutcome.imported
        assert err is None
        assert first_inode == second_inode


class TestImportShowFromTorrent:
    def test_unmatched_video_marks_failed_no_match(self, tmp_path: Path) -> None:
        show = make_show(name="Test Show", year=2020)
        episode = show.seasons[0].episodes[0]
        torrent = make_torrent(title="Test.Show.S01E01.1080p")
        ef = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        show_repo.episode_files[ef.id] = ef
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]

        completed = tmp_path / "completed"
        torrent_dir = completed / torrent.title
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "Test.Show.S02E05.1080p.mkv").write_bytes(b"x")

        svc, repo, _ = build_show_service(
            show_repo=show_repo, torrent_repo=torrent_repo
        )

        with ExitStack() as stack:
            for p in _patch_config(tmp_path, completed=completed):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(svc._run_import_show_from_torrent(show=show, torrent=torrent))

        updated = repo.episode_files[ef.id]
        assert updated.import_status == ImportOutcome.failed_no_match
        assert updated.import_error == "No matching video file"
        assert updated.attempt_count == 1

    def test_matched_video_imports_via_torrent_flow(self, tmp_path: Path) -> None:
        show = make_show(name="Test Show", year=2020)
        episode = show.seasons[0].episodes[0]
        torrent = make_torrent(title="Test.Show.S01E01.1080p")
        ef = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        show_repo.episode_files[ef.id] = ef
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef]

        completed = tmp_path / "completed"
        torrent_dir = completed / torrent.title
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "Test.Show.S01E01.1080p.mkv").write_bytes(b"video")

        svc, repo, _ = build_show_service(
            show_repo=show_repo, torrent_repo=torrent_repo
        )

        with ExitStack() as stack:
            for p in _patch_config(tmp_path, completed=completed):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(svc._run_import_show_from_torrent(show=show, torrent=torrent))

        updated = repo.episode_files[ef.id]
        assert updated.import_status == ImportOutcome.imported
        assert updated.attempt_count >= 1

    def test_multi_episode_torrent_import_batches_episode_lookup(
        self, tmp_path: Path
    ) -> None:
        show = _show_with_two_episodes()
        season = show.seasons[0]
        episode1, episode2 = season.episodes
        torrent = make_torrent(title="Test.Show.S01")
        ef1 = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode1.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )
        ef2 = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode2.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        show_repo.episode_files[ef1.id] = ef1
        show_repo.episode_files[ef2.id] = ef2
        _wrap_show_repo_lookup_counters(show_repo)
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef1, ef2]

        completed = tmp_path / "completed"
        torrent_dir = completed / torrent.title
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "Test.Show.S01E01.1080p.mkv").write_bytes(b"video1")
        (torrent_dir / "Test.Show.S01E02.1080p.mkv").write_bytes(b"video2")

        svc, repo, _ = build_show_service(
            show_repo=show_repo, torrent_repo=torrent_repo
        )

        with ExitStack() as stack:
            for p in _patch_config(tmp_path, completed=completed):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(svc._run_import_show_from_torrent(show=show, torrent=torrent))

        assert show_repo.get_episodes_with_seasons_calls == 1
        assert show_repo.get_episode_calls == 0
        assert show_repo.get_season_by_episode_calls == 0
        assert repo.episode_files[ef1.id].import_status == ImportOutcome.imported
        assert repo.episode_files[ef2.id].import_status == ImportOutcome.imported

    def test_unmatched_episode_still_marks_failed_no_match_with_batch_lookup(
        self, tmp_path: Path
    ) -> None:
        show = _show_with_two_episodes()
        season = show.seasons[0]
        episode1, episode2 = season.episodes
        torrent = make_torrent(title="Test.Show.S01")
        ef1 = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode1.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )
        ef2 = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode2.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )

        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        show_repo.episode_files[ef1.id] = ef1
        show_repo.episode_files[ef2.id] = ef2
        _wrap_show_repo_lookup_counters(show_repo)
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.episode_files[torrent.id] = [ef1, ef2]

        completed = tmp_path / "completed"
        torrent_dir = completed / torrent.title
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "Test.Show.S01E01.1080p.mkv").write_bytes(b"video1")
        (torrent_dir / "Test.Show.S02E05.1080p.mkv").write_bytes(b"nomatch")

        svc, repo, _ = build_show_service(
            show_repo=show_repo, torrent_repo=torrent_repo
        )

        with ExitStack() as stack:
            for p in _patch_config(tmp_path, completed=completed):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(svc._run_import_show_from_torrent(show=show, torrent=torrent))

        assert show_repo.get_episodes_with_seasons_calls == 1
        assert show_repo.get_episode_calls == 0
        assert show_repo.get_season_by_episode_calls == 0
        assert repo.episode_files[ef1.id].import_status == ImportOutcome.imported
        updated = repo.episode_files[ef2.id]
        assert updated.import_status == ImportOutcome.failed_no_match
        assert updated.import_error == "No matching video file"


class TestImportShowFromDirectory:
    def test_scan_import_batches_season_file_fetch(self, tmp_path: Path) -> None:
        show = _show_with_two_seasons()
        show_repo = FakeShowRepository()
        show_repo.add_show(show)
        _wrap_show_repo_lookup_counters(show_repo)

        source = tmp_path / "incoming" / "Scan Batch Show (2020)"
        source.mkdir(parents=True)
        # Unmatched names: exercise the scan loop only (no import_episode_from_file).
        (source / "unrelated.video.mkv").write_bytes(b"orphan")

        svc, _, _ = build_show_service(show_repo=show_repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            imported = run_async(svc.import_show_from_directory(show, source))

        assert imported is False
        assert show_repo.get_episode_files_by_season_ids_calls == 1
        assert show_repo.get_episode_files_by_season_id_calls == 0

    def test_scan_import_skips_inode_already_linked(self, tmp_path: Path) -> None:
        show = make_show(name="Inode Show", year=2020)
        season = show.seasons[0]
        episode = season.episodes[0]
        show_repo = FakeShowRepository()
        show_repo.add_show(show)

        source = tmp_path / "incoming" / "Inode Show (2020)"
        source.mkdir(parents=True)
        library_video = source / "Inode.Show.S01E01.1080p.mkv"
        library_video.write_bytes(b"same-bytes")
        alias_video = source / "Inode.Show.S01E01.alt.1080p.mkv"
        alias_video.hardlink_to(library_video)
        assert library_video.stat().st_ino == alias_video.stat().st_ino

        svc, repo, _ = build_show_service(show_repo=show_repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(
                svc.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=library_video,
                    torrent_id=None,
                )
            )
            file_count_before = len(repo.episode_files)
            imported = run_async(svc.import_show_from_directory(show, source))

        assert imported is True
        assert len(repo.episode_files) == file_count_before


class TestImportMovieFromTorrent:
    def test_missing_video_files_mark_failed_io(self, tmp_path: Path) -> None:
        from miramedia.movies.schemas import MovieFile

        movie = make_movie(name="Test Movie", year=2020)
        torrent = make_torrent(title="Test.Movie.2020.1080p")
        mf = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )

        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        movie_repo.movie_files[mf.id] = mf
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.movie_files[torrent.id] = [mf]

        completed = tmp_path / "completed"
        (completed / torrent.title).mkdir(parents=True)

        svc, repo, _ = build_movie_service(
            movie_repo=movie_repo, torrent_repo=torrent_repo
        )

        with ExitStack() as stack:
            for p in _patch_config(tmp_path, completed=completed):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(svc._run_import_movie_from_torrent(movie=movie, torrent=torrent))

        updated = repo.movie_files[mf.id]
        assert updated.import_status == ImportOutcome.failed_io
        assert updated.import_error == "Source files missing on disk."

    def test_single_video_imports_movie_file(self, tmp_path: Path) -> None:
        from miramedia.movies.schemas import MovieFile

        movie = make_movie(name="Test Movie", year=2020)
        torrent = make_torrent(title="Test.Movie.2020.1080p")
        mf = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
        )

        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        movie_repo.movie_files[mf.id] = mf
        torrent_repo = FakeTorrentRepository()
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.movie_files[torrent.id] = [mf]

        completed = tmp_path / "completed"
        torrent_dir = completed / torrent.title
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "Test.Movie.2020.1080p.mkv").write_bytes(b"video" * 1000)

        svc, repo, _ = build_movie_service(
            movie_repo=movie_repo, torrent_repo=torrent_repo
        )

        with ExitStack() as stack:
            for p in _patch_config(tmp_path, completed=completed):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(svc._run_import_movie_from_torrent(movie=movie, torrent=torrent))

        updated = repo.movie_files[mf.id]
        assert updated.import_status == ImportOutcome.imported

"""Characterization tests for movie import-from-file, torrent, and directory flows."""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from miramedia.file_status import ImportOutcome
from miramedia.imports.files import DiskSpaceError
from miramedia.movies.schemas import MovieFile
from miramedia.torrents.mediainfo import MediaFileInfo
from miramedia.torrents.schemas import Quality
from tests.fakes import build_movie_service, run_async
from tests.fakes.config import fake_config
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeTorrentRepository,
    make_movie,
    make_torrent,
)

_CONFIG_PATCH_TARGETS = (
    "miramedia.config.MiraMediaConfig",
    "miramedia.naming.MiraMediaConfig",
    "miramedia.shows.service.MiraMediaConfig",
    "miramedia.movies.service.MiraMediaConfig",
    "miramedia.torrents.paths.MiraMediaConfig",
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
            __import__(
                "miramedia.movies.service", fromlist=["MovieService"]
            ).MovieService,
            "_trigger_subtitle_search_for_movie",
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


class TestImportMovieFromFile:
    def test_fresh_import_hardlinks_and_inserts_row(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        assert len(movie_repo.movie_files) == 1
        linked = list((tmp_path / "movies").rglob("*.mkv"))
        assert len(linked) == 1
        src_stat = source.stat()
        linked_stat = linked[0].stat()
        assert linked_stat.st_ino == src_stat.st_ino
        assert linked_stat.st_dev == src_stat.st_dev

    def test_finalize_link_time_row_without_duplicating(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        torrent = make_torrent(title="Test.Movie.2020.1080p")
        existing_id = uuid.uuid4()
        pending = MovieFile(
            id=existing_id,
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=torrent.id,
            import_status=ImportOutcome.pending,
        )

        repo = FakeMovieRepository()
        repo.add_movie(movie)
        repo.movie_files[existing_id] = pending

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    torrent_id=torrent.id,
                    existing_file_id=existing_id,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        assert len(movie_repo.movie_files) == 1
        finalized = movie_repo.movie_files[existing_id]
        assert finalized.import_status == ImportOutcome.imported
        assert finalized.codec == "h264"

    def test_same_quality_collision_gets_extra_discriminator(
        self, tmp_path: Path
    ) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source1 = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source2 = tmp_path / "incoming" / "Test.Movie.2020.1080p.alt.mkv"
        source1.parent.mkdir(parents=True)
        source1.write_bytes(b"video-one-content")
        source2.write_bytes(b"video-two-content-longer")

        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source1,
                    torrent_id=None,
                )
            )
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source2,
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        assert len(movie_repo.movie_files) == 2
        extras = {row.extra for row in movie_repo.movie_files.values()}
        assert extras == {"", "2"}
        mkv_names = {p.name for p in (tmp_path / "movies").rglob("*.mkv")}
        assert len(mkv_names) == 2

    def test_disk_space_error_returns_failed_io(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            stack.enter_context(
                patch(
                    "miramedia.movies.service.link_video_into_slot",
                    side_effect=DiskSpaceError("no space left"),
                )
            )
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.failed_io
        assert err == "no space left"
        assert len(movie_repo.movie_files) == 0

    def test_subtitle_linking_skips_missing_language(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")
        matched = tmp_path / "incoming" / "Test.Movie.2020.en.srt"
        unmatched = tmp_path / "incoming" / "Test.Movie.2020.forced.srt"
        matched.write_bytes(b"matched")
        unmatched.write_bytes(b"forced-no-lang")

        svc, _, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    subtitle_files=[matched, unmatched],
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        linked_subs = sorted((tmp_path / "movies").rglob("*.srt"))
        assert len(linked_subs) == 1
        assert linked_subs[0].name.endswith(".en.srt")

    def test_subtitle_linking_disambiguates_same_language_collisions(
        self, tmp_path: Path
    ) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")
        sub1 = tmp_path / "incoming" / "Test.Movie.2020.en.srt"
        sub2 = tmp_path / "incoming" / "Another.en.srt"
        sub1.write_bytes(b"sub-one")
        sub2.write_bytes(b"sub-two")

        svc, _, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    subtitle_files=[sub1, sub2],
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        linked_subs = sorted((tmp_path / "movies").rglob("*.srt"))
        assert len(linked_subs) == 2
        video_stem = next((tmp_path / "movies").rglob("*.mkv")).stem
        names = {p.name for p in linked_subs}
        assert names == {f"{video_stem}.en.srt", f"{video_stem}.en.2.srt"}

    def test_subtitle_linking_disambiguates_three_same_language_collisions(
        self, tmp_path: Path
    ) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")
        sub1 = tmp_path / "incoming" / "one.en.srt"
        sub2 = tmp_path / "incoming" / "two.en.srt"
        sub3 = tmp_path / "incoming" / "three.en.srt"
        sub1.write_bytes(b"1")
        sub2.write_bytes(b"2")
        sub3.write_bytes(b"3")

        svc, _, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    subtitle_files=[sub1, sub2, sub3],
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        video_stem = next((tmp_path / "movies").rglob("*.mkv")).stem
        names = {p.name for p in (tmp_path / "movies").rglob("*.srt")}
        assert names == {
            f"{video_stem}.en.srt",
            f"{video_stem}.en.2.srt",
            f"{video_stem}.en.3.srt",
        }

    def test_source_in_place_skips_subtitle_linking(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        svc, _, _ = build_movie_service(movie_repo=repo)
        movie_root = svc.get_movie_root_path(movie=movie, write=True)
        movie_root.mkdir(parents=True, exist_ok=True)

        source = movie_root / "Test.Movie.2020.1080p.mkv"
        source.write_bytes(b"video-bytes")
        sub = tmp_path / "incoming" / "Test.Movie.2020.en.srt"
        sub.parent.mkdir(parents=True)
        sub.write_bytes(b"sub")

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            outcome, err = run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    subtitle_files=[sub],
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        assert list(movie_root.glob("*.srt")) == []

    def test_session_released_before_subtitle_file_io(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source = tmp_path / "incoming" / "Test.Movie.2020.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")
        sub = tmp_path / "incoming" / "Test.Movie.2020.en.srt"
        sub.write_bytes(b"sub")

        svc, _, _ = build_movie_service(movie_repo=repo)
        events: list[str] = []
        release_calls = 0
        from miramedia.imports.files import import_file as real_import_file

        async def _track_release(*_args: object, **_kwargs: object) -> None:
            nonlocal release_calls
            release_calls += 1
            events.append(f"release:{release_calls}")

        def _track_import_file(*, target_file: Path, source_file: Path) -> None:
            if target_file.suffix == ".mkv":
                events.append("video_io")
            elif target_file.suffix == ".srt":
                events.append("subtitle_io")
            real_import_file(target_file=target_file, source_file=source_file)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "miramedia.database.release_session_before_external_io",
                    side_effect=_track_release,
                )
            )
            stack.enter_context(
                patch(
                    "miramedia.media_state.refresh_media_state", new_callable=AsyncMock
                )
            )
            stack.enter_context(
                patch(
                    "miramedia.movies.service.analyze_async",
                    new_callable=AsyncMock,
                    return_value=MediaFileInfo(
                        quality=Quality.fullhd, video_codec="h264"
                    ),
                )
            )
            stack.enter_context(
                patch("miramedia.movies.service.invalidate_disk_scan_cache")
            )
            stack.enter_context(
                patch.object(
                    __import__(
                        "miramedia.movies.service", fromlist=["MovieService"]
                    ).MovieService,
                    "_trigger_subtitle_search_for_movie",
                    new_callable=AsyncMock,
                )
            )
            stack.enter_context(
                patch.object(
                    __import__(
                        "miramedia.movies.service", fromlist=["MovieService"]
                    ).MovieService,
                    "_trigger_bazarr_notify_for_movie",
                    new_callable=AsyncMock,
                )
            )
            stack.enter_context(
                patch(
                    "miramedia.imports.files.import_file",
                    side_effect=_track_import_file,
                )
            )
            run_async(
                svc.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    subtitle_files=[sub],
                    torrent_id=None,
                )
            )

        assert release_calls == 3
        assert events.index("release:2") < events.index("video_io")
        assert events.index("video_io") < events.index("release:3")
        assert events.index("release:3") < events.index("subtitle_io")


class TestImportMovieFromTorrent:
    def test_video_imported_and_junk_ignored(self, tmp_path: Path) -> None:
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
        (torrent_dir / "readme.txt").write_text("ignore me")

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
        assert list((tmp_path / "movies").rglob("*.mkv"))
        assert not list((tmp_path / "movies").rglob("readme.txt"))

    def test_missing_video_files_mark_failed_io(self, tmp_path: Path) -> None:
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


class TestImportMovieFromDirectory:
    def test_directory_import_links_matched_video(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source_dir = tmp_path / "incoming" / "Test Movie (2020)"
        source_dir.mkdir(parents=True)
        (source_dir / "Test.Movie.2020.1080p.mkv").write_bytes(b"video-bytes")

        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            imported = run_async(svc.import_movie_from_directory(movie, source_dir))

        assert imported is True
        assert len(movie_repo.movie_files) == 1
        assert list((tmp_path / "movies").rglob("*.mkv"))

    def test_directory_import_ignores_non_video_files(self, tmp_path: Path) -> None:
        movie = make_movie(name="Test Movie", year=2020)
        repo = FakeMovieRepository()
        repo.add_movie(movie)

        source_dir = tmp_path / "incoming" / "Test Movie (2020)"
        source_dir.mkdir(parents=True)
        (source_dir / "Test.Movie.2020.1080p.mkv").write_bytes(b"video-bytes")
        (source_dir / "poster.jpg").write_bytes(b"jpg")

        svc, movie_repo, _ = build_movie_service(movie_repo=repo)

        with ExitStack() as stack:
            for p in _patch_config(tmp_path):
                stack.enter_context(p)
            _enter_patches(stack)
            imported = run_async(svc.import_movie_from_directory(movie, source_dir))

        assert imported is True
        assert len(movie_repo.movie_files) == 1
        movie_root = svc.get_movie_root_path(movie=movie, write=False)
        assert not list(movie_root.glob("poster.jpg"))

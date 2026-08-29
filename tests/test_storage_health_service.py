"""DB-free storage-health service tests: counts, overlay, session release."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from miramedia.config import BasicConfig
from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieFile
from miramedia.shows.schemas import EpisodeFile
from miramedia.storage.repository import StorageHealthPage
from miramedia.storage.service import StorageHealthService
from miramedia.storage.states import STATE_RANK, classify_sql_state
from miramedia.torrents.integrity import Sha1MismatchPageKey
from miramedia.torrents.schemas import Quality
from tests.fakes.db import FakeDb
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    make_movie,
    make_show,
)


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Cfg:
    misc: BasicConfig


class FakeStorageHealthRepository:
    def __init__(
        self, show_repo: FakeShowRepository, movie_repo: FakeMovieRepository
    ) -> None:
        self.db = show_repo.db
        self.show_repo = show_repo
        self.movie_repo = movie_repo

    def _rows(self) -> list[tuple[str, EpisodeFile | MovieFile]]:
        out: list[tuple[str, EpisodeFile | MovieFile]] = [
            ("show", row) for row in self.show_repo.episode_files.values()
        ]
        out.extend(("movie", row) for row in self.movie_repo.movie_files.values())
        return out

    async def count_buckets(self) -> dict[str, int]:
        counts = {
            "imported": 0,
            "healthy": 0,
            "unknown": 0,
            "corrupt": 0,
            "orphaned": 0,
            "pending": 0,
        }
        for _kind, row in self._rows():
            sql = classify_sql_state(
                import_status=row.import_status,
                import_error=row.import_error,
                sha1=row.sha1,
                torrent_id=row.torrent_id,
            )
            if row.import_status == ImportOutcome.imported:
                counts["imported"] += 1
            counts[sql] += 1
        return counts

    async def paginate_keys(
        self,
        *,
        offset: int,
        limit: int,
        state: str | None = None,
        media_type: str | None = None,
        q: str | None = None,
    ) -> StorageHealthPage:
        keyed: list[tuple[int, UUID, Sha1MismatchPageKey]] = []
        for kind, row in self._rows():
            if media_type and kind != media_type:
                continue
            sql = classify_sql_state(
                import_status=row.import_status,
                import_error=row.import_error,
                sha1=row.sha1,
                torrent_id=row.torrent_id,
            )
            if state and sql != state:
                continue
            if q:
                title = ""
                if kind == "show":
                    from tests.fakes.repositories import _season_for_episode

                    try:
                        season = _season_for_episode(self.show_repo, row.episode_id)
                        title = self.show_repo.shows[season.show_id].name
                    except Exception:
                        title = ""
                else:
                    movie = self.movie_repo.movies.get(row.movie_id)
                    title = movie.name if movie is not None else ""
                if q.lower() not in title.lower():
                    continue
            keyed.append(
                (
                    STATE_RANK[sql],
                    row.id,
                    Sha1MismatchPageKey(media_type=kind, file_id=row.id),  # type: ignore[arg-type]
                )
            )
        keyed.sort(key=lambda item: (item[0], item[1]))
        total = len(keyed)
        page = keyed[offset : offset + limit]
        return StorageHealthPage(keys=[item[2] for item in page], total=total)

    async def get_episode_files_by_ids(
        self, file_ids: list[UUID]
    ) -> dict[UUID, EpisodeFile]:
        return {
            fid: self.show_repo.episode_files[fid]
            for fid in file_ids
            if fid in self.show_repo.episode_files
        }

    async def get_movie_files_by_ids(
        self, file_ids: list[UUID]
    ) -> dict[UUID, MovieFile]:
        return {
            fid: self.movie_repo.movie_files[fid]
            for fid in file_ids
            if fid in self.movie_repo.movie_files
        }

    async def list_title_library_names(self) -> list[str]:
        names = {show.library for show in self.show_repo.shows.values()}
        names.update(movie.library for movie in self.movie_repo.movies.values())
        return sorted(
            name for name in names if name and name.strip() and name != "Default"
        )


def _service(
    show_repo: FakeShowRepository,
    movie_repo: FakeMovieRepository,
    tmp_path: Path,
    *,
    integrity_enabled: bool = False,
) -> StorageHealthService:
    shows = tmp_path / "shows"
    movies = tmp_path / "movies"
    shows.mkdir(exist_ok=True)
    movies.mkdir(exist_ok=True)
    cfg = _Cfg(
        misc=BasicConfig(
            show_directory=shows,
            movie_directory=movies,
            integrity_check_enabled=integrity_enabled,
            integrity_check_interval_hours=168,
        )
    )
    db = FakeDb()
    show_repo.db = db
    movie_repo.db = db
    repo = FakeStorageHealthRepository(show_repo, movie_repo)
    repo.db = db
    return StorageHealthService(
        db,  # type: ignore[arg-type]
        show_repository=show_repo,  # type: ignore[arg-type]
        movie_repository=movie_repo,  # type: ignore[arg-type]
        repository=repo,  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
    )


def test_summary_unknown_not_healthy_when_integrity_off(tmp_path: Path) -> None:
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    show = make_show()
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    for _ in range(10):
        fid = uuid.uuid4()
        show_repo.episode_files[fid] = EpisodeFile(
            id=fid,
            episode_id=episode.id,
            quality=Quality.fullhd,
            torrent_id=uuid.uuid4(),
            import_status=ImportOutcome.imported,
            sha1=None,
        )
    svc = _service(show_repo, movie_repo, tmp_path, integrity_enabled=False)
    summary = _run(svc.get_summary())
    assert summary.counts.unknown == 10
    assert summary.counts.healthy == 0
    assert summary.counts.corrupt == 0
    assert summary.counts.missing is None
    assert summary.integrity_check_enabled is False
    assert summary.volumes
    assert any(volume.label.startswith("Shows") for volume in summary.volumes)


def test_summary_counts_corrupt_and_does_not_double_count_orphans(
    tmp_path: Path,
) -> None:
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    show = make_show()
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    corrupt_id = uuid.uuid4()
    show_repo.episode_files[corrupt_id] = EpisodeFile(
        id=corrupt_id,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
    )
    orphan_id = uuid.uuid4()
    show_repo.episode_files[orphan_id] = EpisodeFile(
        id=orphan_id,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=None,
        import_status=ImportOutcome.failed_io,
        import_error="io",
    )
    pending_id = uuid.uuid4()
    show_repo.episode_files[pending_id] = EpisodeFile(
        id=pending_id,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.pending,
    )
    svc = _service(show_repo, movie_repo, tmp_path)
    summary = _run(svc.get_summary())
    assert summary.counts.corrupt == 1
    assert summary.counts.orphaned == 1
    assert summary.counts.pending == 1


def test_wanted_title_without_file_row_is_absent(tmp_path: Path) -> None:
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    movie_repo.add_movie(make_movie(name="Wanted"))
    svc = _service(show_repo, movie_repo, tmp_path)
    page = _run(svc.list_files(offset=0, limit=50))
    assert page.total == 0
    assert page.items == []


def test_mismatch_unresolved_path_ok_root_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    show = make_show(name="Severance")
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_episode_paths_async",
        AsyncMock(return_value={fid: None}),
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_movie_paths_async",
        AsyncMock(return_value={}),
    )
    svc = _service(show_repo, movie_repo, tmp_path)
    detail = _run(svc.get_file(media_type="show", file_id=fid))
    assert detail.state == "missing"


def test_dead_root_is_inaccessible_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    show = make_show()
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.imported,
        sha1="abc",
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_episode_paths_async",
        AsyncMock(return_value={fid: None}),
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_movie_paths_async",
        AsyncMock(return_value={}),
    )
    svc = _service(show_repo, movie_repo, tmp_path)
    svc._config.misc.show_directory = tmp_path / "gone"  # type: ignore[union-attr]
    detail = _run(svc.get_file(media_type="show", file_id=fid))
    assert detail.state == "inaccessible"


def test_list_releases_session_before_path_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    show = make_show()
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.imported,
        sha1="abc",
    )

    async def _release(*_sessions: Any) -> None:
        order.append("release")

    async def _resolve_ep(*_args: Any, **_kwargs: Any) -> dict[UUID, Path | None]:
        order.append("resolve")
        return {fid: tmp_path / "file.mkv"}

    monkeypatch.setattr(
        "miramedia.storage.service.release_sessions_before_external_io",
        _release,
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_episode_paths_async",
        _resolve_ep,
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_movie_paths_async",
        AsyncMock(return_value={}),
    )
    svc = _service(show_repo, movie_repo, tmp_path)
    page = _run(svc.list_files(offset=0, limit=50))
    assert order == ["release", "resolve"]
    assert page.items[0].state == "healthy"
    assert page.items[0].path is not None


def test_unconfigured_library_is_not_marked_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()
    show = make_show()
    show.library = "NAS-unconfigured"
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.imported,
        sha1="abc",
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_episode_paths_async",
        AsyncMock(return_value={fid: None}),
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_movie_paths_async",
        AsyncMock(return_value={}),
    )
    svc = _service(show_repo, movie_repo, tmp_path)
    detail = _run(svc.get_file(media_type="show", file_id=fid))
    assert detail.state == "healthy"
    summary = _run(svc.get_summary())
    assert "NAS-unconfigured" in summary.unconfigured_library_names


def test_get_summary_offloads_storage_probes_after_session_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miramedia.storage.service as storage_service

    order: list[str] = []
    show_repo = FakeShowRepository()
    movie_repo = FakeMovieRepository()

    async def _release(*_sessions: Any) -> None:
        order.append("release")

    real_probe = storage_service.probe_storage_summary

    def _probe_sentinel(misc: BasicConfig) -> object:
        order.append("probe")
        return real_probe(misc)

    monkeypatch.setattr(
        "miramedia.storage.service.release_sessions_before_external_io",
        _release,
    )
    monkeypatch.setattr(storage_service, "probe_storage_summary", _probe_sentinel)
    svc = _service(show_repo, movie_repo, tmp_path)

    with patch(
        "miramedia.storage.service.asyncio.to_thread",
        wraps=asyncio.to_thread,
    ) as mock_to_thread:
        summary = _run(svc.get_summary())

    mock_to_thread.assert_called_once()
    assert mock_to_thread.call_args.args[0] is _probe_sentinel
    assert order == ["release", "probe"]
    assert summary.volumes


def test_summary_source_forbids_hash_and_media_walk() -> None:
    src = inspect.getsource(StorageHealthService.get_summary)
    assert "compute_sha1" not in src
    assert "iterdir" not in src
    assert "batch_resolve" not in src
    import miramedia.storage.service as mod

    assert "compute_sha1" not in inspect.getsource(mod)

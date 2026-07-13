"""Compare-and-set guards for integrity audit writes and mismatch actions."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

import miramedia.scheduler as scheduler
from miramedia.exceptions import ConflictError, NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.movies.models import MovieFile
from miramedia.movies.repository import MovieRepository
from miramedia.shows.models import EpisodeFile
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.torrents.integrity import (
    integrity_audit_snapshot_where,
    integrity_mismatch_action_where,
)
from miramedia.torrents.schemas import MediaType, Quality
from miramedia.torrents.service import TorrentService
from tests.fakes.config import fake_scheduler_config
from tests.fakes.repositories import FakeTorrentRepository, make_show
from tests.fakes.scheduler import (
    FakeMoviePathService,
    FakeShowService,
    bg_movie_path_service_factory,
    bg_show_service_factory,
)
from tests.test_integrity_mismatch_api import (
    _IntegrityMovieRepo,
    _IntegrityShowRepo,
    _movie_service,
    _show_service,
)


def _run(coro) -> None:
    asyncio.run(coro)


def _compile_sql(clause: Any) -> str:
    return str(
        clause.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_audit_snapshot_predicate_imported_null_sha1_and_null_error() -> None:
    file_id = uuid.uuid4()
    where = integrity_audit_snapshot_where(
        EpisodeFile,
        file_id,
        expected_sha1=None,
        expected_import_error=None,
    )
    sql = _compile_sql(where)
    assert f"episode_file.id = '{file_id}'" in sql
    assert "episode_file.import_status" in sql
    assert "episode_file.sha1 IS NULL" in sql
    assert "episode_file.import_error IS NULL" in sql


def test_audit_snapshot_predicate_imported_non_null_sha1_and_error() -> None:
    file_id = uuid.uuid4()
    prior = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    prior_error = "sha1 mismatch (expected a…, got b…)"
    where = integrity_audit_snapshot_where(
        EpisodeFile,
        file_id,
        expected_sha1=prior,
        expected_import_error=prior_error,
    )
    sql = _compile_sql(where)
    assert sql.count("IS NOT DISTINCT FROM") == 2
    assert prior in sql
    assert prior_error in sql


def test_mismatch_action_predicate_requires_imported_mismatch_stamp() -> None:
    file_id = uuid.uuid4()
    where = integrity_mismatch_action_where(MovieFile, file_id)
    sql = _compile_sql(where)
    assert f"movie_file.id = '{file_id}'" in sql
    assert "movie_file.import_status" in sql
    assert "movie_file.import_error LIKE" in sql
    assert "sha1 mismatch%" in sql


@dataclass
class _RowcountResult:
    rowcount: int


@dataclass
class _MutableRow:
    id: uuid.UUID
    sha1: str | None
    import_status: ImportOutcome = ImportOutcome.imported
    import_error: str | None = None
    _resolved_path: Path | None = None


@dataclass
class _ScalarResult:
    rows: list[Any]

    def all(self) -> list[Any]:
        return self.rows


@dataclass
class _SelectExecuteResult:
    rows: list[Any]

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self.rows)


def _row_matches_audit_snapshot(
    row: _MutableRow,
    *,
    file_id: uuid.UUID,
    expected_sha1: str | None,
    expected_import_error: str | None,
) -> bool:
    if row.id != file_id or row.import_status != ImportOutcome.imported:
        return False
    if expected_sha1 is None:
        if row.sha1 is not None:
            return False
    elif row.sha1 != expected_sha1:
        return False
    if expected_import_error is None:
        if row.import_error is not None:
            return False
    elif row.import_error != expected_import_error:
        return False
    return True


def _iter_where_parts(stmt: Update):
    for criterion in stmt._where_criteria:
        children = (
            list(criterion.get_children())
            if hasattr(criterion, "get_children")
            else [criterion]
        )
        yield from children


class _CompareAndSetSession:
    """Applies audit updates only when the full pre-hash snapshot still matches."""

    def __init__(
        self,
        *,
        episode_rows: list[_MutableRow],
        movie_rows: list[_MutableRow],
    ) -> None:
        self.episode_rows = {row.id: row for row in episode_rows}
        self.movie_rows = {row.id: row for row in movie_rows}
        self.updates: list[Update] = []

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _RowcountResult:
        if not isinstance(stmt, Update):
            return _RowcountResult(0)
        self.updates.append(stmt)
        table_name = stmt.table.name
        rows = (
            self.episode_rows
            if table_name == EpisodeFile.__tablename__
            else self.movie_rows
            if table_name == MovieFile.__tablename__
            else None
        )
        if rows is None:
            return _RowcountResult(0)

        file_id = None
        expected_sha1: str | None | object = object()
        expected_import_error: str | None | object = object()
        values = {key.key: value.value for key, value in stmt._values.items()}
        for criterion in _iter_where_parts(stmt):
            compiled = _compile_sql(criterion)
            if ".id =" in compiled:
                file_id = uuid.UUID(compiled.split("'")[1])
            if ".sha1 IS NULL" in compiled:
                expected_sha1 = None
            elif ".sha1 IS NOT DISTINCT FROM" in compiled:
                expected_sha1 = compiled.split("'")[1]
            if ".import_error IS NULL" in compiled:
                expected_import_error = None
            elif ".import_error IS NOT DISTINCT FROM" in compiled:
                expected_import_error = compiled.split("'")[1]

        if file_id is None or file_id not in rows:
            return _RowcountResult(0)
        row = rows[file_id]
        if expected_sha1 is object() or expected_import_error is object():
            return _RowcountResult(0)
        if not _row_matches_audit_snapshot(
            row,
            file_id=file_id,
            expected_sha1=expected_sha1,  # type: ignore[arg-type]
            expected_import_error=expected_import_error,  # type: ignore[arg-type]
        ):
            return _RowcountResult(0)

        if "sha1" in values:
            row.sha1 = values["sha1"]
        if "import_error" in values:
            row.import_error = values["import_error"]
        return _RowcountResult(1)


def _audit_background_session_factory(
    *,
    snapshot_episode_rows: list[_MutableRow] | None = None,
    snapshot_movie_rows: list[_MutableRow] | None = None,
    write_episode_rows: list[_MutableRow] | None = None,
    write_movie_rows: list[_MutableRow] | None = None,
) -> tuple[Any, list[_CompareAndSetSession]]:
    ep_snapshot = list(snapshot_episode_rows or [])
    mv_snapshot = list(snapshot_movie_rows or [])
    write_sessions: list[_CompareAndSetSession] = []
    call = {"n": 0}

    @asynccontextmanager
    async def _background_session():
        call["n"] += 1
        if call["n"] == 1:

            class _SnapshotSession:
                async def commit(self) -> None:
                    return None

                async def rollback(self) -> None:
                    return None

                async def execute(self, stmt: Any) -> _SelectExecuteResult:
                    entity = stmt.column_descriptions[0].get("entity")
                    entity_name = getattr(entity, "__name__", "")
                    if entity_name == "EpisodeFile":
                        return _SelectExecuteResult(ep_snapshot)
                    if entity_name == "MovieFile":
                        return _SelectExecuteResult(mv_snapshot)
                    return _SelectExecuteResult([])

            yield _SnapshotSession()
            return

        write = _CompareAndSetSession(
            episode_rows=list(write_episode_rows or ep_snapshot),
            movie_rows=list(write_movie_rows or mv_snapshot),
        )
        write_sessions.append(write)
        yield write

    return _background_session, write_sessions


def _patch_integrity_config(monkeypatch, *, enabled: bool = True) -> None:
    monkeypatch.setattr(
        "miramedia.config.MiraMediaConfig",
        lambda: fake_scheduler_config(integrity_check_enabled=enabled),
    )


async def _return_sha(sha: str | None):
    return sha


@pytest.mark.parametrize("applied", [True, False])
def test_show_repository_baseline_returns_rowcount(applied: bool) -> None:
    file_id = uuid.uuid4()

    async def _run_repo() -> bool:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_RowcountResult(1 if applied else 0))
        db.flush = AsyncMock()
        repo = ShowRepository(db)
        return await repo.apply_integrity_baseline_if_current(
            file_id,
            expected_sha1=None,
            expected_import_error=None,
            new_sha1="new-sha",
        )

    assert asyncio.run(_run_repo()) is applied


@pytest.mark.parametrize("applied", [True, False])
def test_movie_repository_mismatch_returns_rowcount(applied: bool) -> None:
    file_id = uuid.uuid4()

    async def _run_repo() -> bool:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_RowcountResult(1 if applied else 0))
        db.flush = AsyncMock()
        repo = MovieRepository(db)
        return await repo.stamp_integrity_mismatch_if_current(
            file_id,
            expected_sha1="prior-sha",
            expected_import_error="sha1 mismatch (expected prior…, got next…)",
            import_error="sha1 mismatch (expected prior…, got next…)",
        )

    assert asyncio.run(_run_repo()) is applied


def test_show_audit_skips_when_dismiss_cleared_import_error_after_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "show-dismissed.mkv"
    media_path.write_bytes(b"content")
    prior = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    prior_error = "sha1 mismatch (expected bbbbbbbbbb…, got old…)"
    snapshot_row = _MutableRow(
        id=file_id,
        sha1=prior,
        import_error=prior_error,
        _resolved_path=media_path,
    )
    write_row = _MutableRow(
        id=file_id,
        sha1=prior,
        import_error=None,
        _resolved_path=media_path,
    )
    bg_session, write_sessions = _audit_background_session_factory(
        snapshot_episode_rows=[snapshot_row],
        write_episode_rows=[write_row],
    )
    show_service = FakeShowService(make_show(), path_by_row_id={file_id: media_path})

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.database.background_session", bg_session)
    monkeypatch.setattr(
        "miramedia.database.bg_show_service",
        bg_show_service_factory(show_service),
    )
    monkeypatch.setattr(
        "miramedia.database.bg_movie_service",
        bg_movie_path_service_factory(FakeMoviePathService()),
    )
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("cccccccccccccccccccccccccccccccccccccccc"),
    )

    _run(scheduler.verify_imported_files_task())

    write_session = write_sessions[-1]
    assert write_row.import_error is None
    assert len(write_session.updates) == 1


def test_movie_audit_skips_when_dismiss_cleared_import_error_after_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "movie-dismissed.mkv"
    media_path.write_bytes(b"content")
    prior = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    prior_error = "sha1 mismatch (expected bbbbbbbbbb…, got old…)"
    snapshot_row = _MutableRow(
        id=file_id,
        sha1=prior,
        import_error=prior_error,
        _resolved_path=media_path,
    )
    write_row = _MutableRow(
        id=file_id,
        sha1=prior,
        import_error=None,
        _resolved_path=media_path,
    )
    bg_session, write_sessions = _audit_background_session_factory(
        snapshot_movie_rows=[snapshot_row],
        write_movie_rows=[write_row],
    )
    movie_service = FakeMoviePathService(path_by_row_id={file_id: media_path})

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.database.background_session", bg_session)
    monkeypatch.setattr(
        "miramedia.database.bg_show_service",
        bg_show_service_factory(FakeShowService(make_show())),
    )
    monkeypatch.setattr(
        "miramedia.database.bg_movie_service",
        bg_movie_path_service_factory(movie_service),
    )
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("cccccccccccccccccccccccccccccccccccccccc"),
    )

    _run(scheduler.verify_imported_files_task())

    write_session = write_sessions[-1]
    assert write_row.import_error is None
    assert len(write_session.updates) == 1


def test_show_audit_skips_when_rebaseline_changed_sha1_after_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "show-rebaselined.mkv"
    media_path.write_bytes(b"content")
    snapshot_row = _MutableRow(
        id=file_id,
        sha1=None,
        import_error="sha1 mismatch (expected a…, got b…)",
        _resolved_path=media_path,
    )
    write_row = _MutableRow(
        id=file_id,
        sha1=None,
        import_error=None,
        _resolved_path=media_path,
    )
    bg_session, write_sessions = _audit_background_session_factory(
        snapshot_episode_rows=[snapshot_row],
        write_episode_rows=[write_row],
    )
    show_service = FakeShowService(make_show(), path_by_row_id={file_id: media_path})

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.database.background_session", bg_session)
    monkeypatch.setattr(
        "miramedia.database.bg_show_service",
        bg_show_service_factory(show_service),
    )
    monkeypatch.setattr(
        "miramedia.database.bg_movie_service",
        bg_movie_path_service_factory(FakeMoviePathService()),
    )
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )

    _run(scheduler.verify_imported_files_task())

    write_session = write_sessions[-1]
    assert write_row.sha1 is None
    assert write_row.import_error is None
    assert len(write_session.updates) == 1


def test_current_audit_baseline_and_mismatch_still_apply(
    monkeypatch, tmp_path: Path
) -> None:
    baseline_id = uuid.uuid4()
    mismatch_id = uuid.uuid4()
    baseline_path = tmp_path / "baseline.mkv"
    mismatch_path = tmp_path / "mismatch.mkv"
    baseline_path.write_bytes(b"baseline")
    mismatch_path.write_bytes(b"mismatch")
    prior = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    baseline_row = _MutableRow(id=baseline_id, sha1=None, _resolved_path=baseline_path)
    mismatch_row = _MutableRow(
        id=mismatch_id,
        sha1=prior,
        import_error=None,
        _resolved_path=mismatch_path,
    )
    bg_session, write_sessions = _audit_background_session_factory(
        snapshot_episode_rows=[baseline_row, mismatch_row],
        write_episode_rows=[baseline_row, mismatch_row],
    )
    show_service = FakeShowService(
        make_show(),
        path_by_row_id={baseline_id: baseline_path, mismatch_id: mismatch_path},
    )

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.database.background_session", bg_session)
    monkeypatch.setattr(
        "miramedia.database.bg_show_service",
        bg_show_service_factory(show_service),
    )
    monkeypatch.setattr(
        "miramedia.database.bg_movie_service",
        bg_movie_path_service_factory(FakeMoviePathService()),
    )

    async def _hash(path: Path) -> str:
        if path == baseline_path:
            return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        return "cccccccccccccccccccccccccccccccccccccccc"

    monkeypatch.setattr(scheduler, "_compute_sha1_async", _hash)

    _run(scheduler.verify_imported_files_task())

    write_session = write_sessions[-1]
    assert baseline_row.sha1 == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert (mismatch_row.import_error or "").startswith("sha1 mismatch")
    assert len(write_session.updates) == 2


def test_rebaseline_conflict_when_mismatch_already_cleared() -> None:
    show = make_show()
    episode = show.seasons[0].episodes[0]
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    file_id = uuid.uuid4()
    show_repo.episode_files[file_id] = EpisodeFileSchema(
        id=file_id,
        episode_id=episode.id,
        quality=Quality.hd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
        import_error=None,
        sha1="abc",
    )

    svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
    with pytest.raises(ConflictError):
        _run(
            svc.rebaseline_file(
                media_type=MediaType.show,
                file_id=file_id,
                show_service=_show_service(show_repo, {}),
                movie_service=_movie_service(_IntegrityMovieRepo(), {}),
            )
        )


def test_dismiss_unknown_id_raises_not_found() -> None:
    svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        _run(
            svc.dismiss_mismatch(
                media_type=MediaType.movie,
                file_id=uuid.uuid4(),
                show_service=_show_service(_IntegrityShowRepo(), {}),
                movie_service=_movie_service(_IntegrityMovieRepo(), {}),
            )
        )


def test_null_sha1_baseline_predicate_distinguishes_from_nonempty_sha1() -> None:
    file_id = uuid.uuid4()
    null_where = integrity_audit_snapshot_where(
        EpisodeFile,
        file_id,
        expected_sha1=None,
        expected_import_error=None,
    )
    nonempty_where = integrity_audit_snapshot_where(
        EpisodeFile,
        file_id,
        expected_sha1="abc",
        expected_import_error="sha1 mismatch (expected a…, got b…)",
    )
    assert "IS NULL" in _compile_sql(null_where)
    assert _compile_sql(nonempty_where).count("IS NOT DISTINCT FROM") == 2

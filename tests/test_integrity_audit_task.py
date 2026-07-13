"""Behavioral tests for ``verify_imported_files_task`` (integrity audit)."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.sql.dml import Update

import miramedia.scheduler as scheduler
from miramedia.movies.models import MovieFile
from miramedia.shows.models import EpisodeFile
from tests.fakes.config import fake_scheduler_config
from tests.fakes.repositories import make_show
from tests.fakes.scheduler import (
    FakeFileRow,
    FakeMoviePathService,
    FakeShowService,
    background_session_factory,
    bg_movie_path_service_factory,
    bg_show_service_factory,
)


def _run(coro) -> None:
    asyncio.run(coro)


def _write_sessions(sessions: list) -> list:
    return [session for session in sessions if session.updates]


def _all_updates(sessions: list):
    return [update for session in sessions for update in session.updates]


def _update_values(stmt: Update) -> dict[str, Any]:
    return {key.key: value.value for key, value in stmt._values.items()}


def _update_table(stmt: Update) -> str:
    return stmt.table.name


def _patch_integrity_config(monkeypatch, *, enabled: bool = True) -> None:
    monkeypatch.setattr(
        "miramedia.config.MiraMediaConfig",
        lambda: fake_scheduler_config(integrity_check_enabled=enabled),
    )


async def _return_sha(sha: str | None):
    return sha


def test_integrity_disabled_skips_background_session(monkeypatch) -> None:
    opened = False

    async def _fail_background_session():
        nonlocal opened
        opened = True
        msg = "background_session should not be called"
        raise AssertionError(msg)

    _patch_integrity_config(monkeypatch, enabled=False)
    monkeypatch.setattr(
        "miramedia.database.background_session", _fail_background_session
    )

    _run(scheduler.verify_imported_files_task())

    assert opened is False


def test_baseline_sha1_written_when_prior_is_none(monkeypatch, tmp_path: Path) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "episode.mkv"
    media_path.write_bytes(b"baseline-content")
    row = FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path)
    bg_session, sessions = background_session_factory(episode_rows=[row])
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
        lambda _path: _return_sha("computed-sha1-abc"),
    )

    _run(scheduler.verify_imported_files_task())

    write_session = _write_sessions(sessions)[0]
    assert len(write_session.updates) == 1
    update = write_session.updates[0]
    assert _update_table(update) == EpisodeFile.__tablename__
    assert _update_values(update) == {"sha1": "computed-sha1-abc"}


def test_matching_prior_sha1_produces_no_update(monkeypatch, tmp_path: Path) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "verified.mkv"
    media_path.write_bytes(b"same-content")
    prior = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    row = FakeFileRow(id=file_id, sha1=prior, _resolved_path=media_path)
    bg_session, sessions = background_session_factory(episode_rows=[row])
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
        lambda _path: _return_sha(prior),
    )

    _run(scheduler.verify_imported_files_task())

    assert _all_updates(sessions) == []


def test_mismatch_stamps_import_error_without_status_change(
    monkeypatch, tmp_path: Path
) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "changed.mkv"
    media_path.write_bytes(b"new-content")
    prior = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    row = FakeFileRow(id=file_id, sha1=prior, _resolved_path=media_path)
    bg_session, sessions = background_session_factory(movie_rows=[row])
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

    write_session = _write_sessions(sessions)[0]
    assert len(write_session.updates) == 1
    update = write_session.updates[0]
    assert _update_table(update) == MovieFile.__tablename__
    values = _update_values(update)
    assert "import_error" in values
    assert "sha1 mismatch" in values["import_error"]
    assert "import_status" not in values


def test_unresolvable_path_skips_row_without_crash(monkeypatch) -> None:
    row = FakeFileRow(id=uuid.uuid4(), sha1=None, _resolved_path=None)
    bg_session, sessions = background_session_factory(episode_rows=[row])
    show_service = FakeShowService(make_show(), path_by_row_id={row.id: None})
    hashed: list[Path] = []

    async def _track_hash(path: Path) -> str:
        hashed.append(path)
        return "hash"

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
    monkeypatch.setattr(scheduler, "_compute_sha1_async", _track_hash)

    _run(scheduler.verify_imported_files_task())

    assert hashed == []
    if len(sessions) > 1:
        assert _all_updates(sessions) == []


def test_hash_io_error_skips_row(monkeypatch, tmp_path: Path) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "on-disk.mkv"
    media_path.write_bytes(b"content")
    row = FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path)
    bg_session, sessions = background_session_factory(episode_rows=[row])
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
        lambda _path: _return_sha(None),
    )

    _run(scheduler.verify_imported_files_task())

    if len(sessions) > 1:
        assert _all_updates(sessions) == []


def test_integrity_audit_no_session_held_during_slow_hash(
    monkeypatch, tmp_path: Path
) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "slow.mkv"
    media_path.write_bytes(b"slow-content")
    row = FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path)
    sessions_open: list[bool] = []

    @asynccontextmanager
    async def _tracking_background_session():
        sessions_open.append(True)
        bg_session, _ = background_session_factory(episode_rows=[row])
        async with bg_session() as session:
            try:
                yield session
            finally:
                sessions_open.pop()

    async def _slow_hash(_path: Path) -> str:
        assert sessions_open == [], "DB session must not be open during hashing"
        await asyncio.sleep(0.01)
        return "slow-hash-sha1-value000000000000000000"

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.database.background_session", _tracking_background_session
    )
    monkeypatch.setattr(
        "miramedia.database.bg_show_service",
        bg_show_service_factory(
            FakeShowService(make_show(), path_by_row_id={file_id: media_path})
        ),
    )
    monkeypatch.setattr(
        "miramedia.database.bg_movie_service",
        bg_movie_path_service_factory(FakeMoviePathService()),
    )
    monkeypatch.setattr(scheduler, "_compute_sha1_async", _slow_hash)

    _run(scheduler.verify_imported_files_task())


def test_integrity_audit_chunks_keyset_reads(monkeypatch, tmp_path: Path) -> None:
    from miramedia.torrents.integrity import INTEGRITY_AUDIT_CHUNK_SIZE

    rows: list[FakeFileRow] = []
    path_by_id: dict[uuid.UUID, Path] = {}
    for i in range(INTEGRITY_AUDIT_CHUNK_SIZE + 5):
        file_id = uuid.UUID(int=i + 1)
        media_path = tmp_path / f"ep-{i}.mkv"
        media_path.write_bytes(f"content-{i}".encode())
        rows.append(FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path))
        path_by_id[file_id] = media_path

    max_select_rows = 0

    @asynccontextmanager
    async def _chunk_observing_session():
        nonlocal max_select_rows
        bg_session, _ = background_session_factory(episode_rows=rows)
        async with bg_session() as session:
            original_execute = session.execute

            async def _execute(stmt):
                nonlocal max_select_rows
                result = await original_execute(stmt)
                from sqlalchemy.sql.selectable import Select

                if isinstance(stmt, Select):
                    entity = stmt.column_descriptions[0].get("entity")
                    if getattr(entity, "__name__", "") == "EpisodeFile":
                        max_select_rows = max(
                            max_select_rows, len(result.scalars().all())
                        )
                return result

            session.execute = _execute  # type: ignore[method-assign]
            yield session

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.database.background_session", _chunk_observing_session
    )
    monkeypatch.setattr(
        "miramedia.database.bg_show_service",
        bg_show_service_factory(
            FakeShowService(make_show(), path_by_row_id=path_by_id)
        ),
    )
    monkeypatch.setattr(
        "miramedia.database.bg_movie_service",
        bg_movie_path_service_factory(FakeMoviePathService()),
    )
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("chunk-hash-000000000000000000000000000000"),
    )

    _run(scheduler.verify_imported_files_task())

    assert max_select_rows <= INTEGRITY_AUDIT_CHUNK_SIZE

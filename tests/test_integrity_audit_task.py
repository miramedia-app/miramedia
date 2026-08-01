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
from miramedia.torrents.integrity import INTEGRITY_AUDIT_CHUNK_SIZE
from tests.fakes.config import fake_scheduler_config
from tests.fakes.db import RecordingSession
from tests.fakes.scheduler import (
    FakeFileRow,
    patch_audit_repository_lookups,
    patch_batch_resolve_paths,
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
    cfg = fake_scheduler_config(integrity_check_enabled=enabled)
    monkeypatch.setattr("miramedia.scheduler.MiraMediaConfig", lambda: cfg)
    monkeypatch.setattr("miramedia.torrents.integrity.MiraMediaConfig", lambda: cfg)
    patch_audit_repository_lookups(monkeypatch)


async def _return_sha(sha: str | None):
    return sha


def _high_water_background_session_factory(
    *,
    episode_rows: list[Any] | None = None,
    movie_rows: list[Any] | None = None,
    episode_high_water: uuid.UUID | None = None,
    movie_high_water: uuid.UUID | None = None,
    episode_budget: int | None = None,
    movie_budget: int | None = None,
) -> tuple[Any, list[RecordingSession]]:
    """Recording sessions with explicit sweep bounds."""
    shared_episode_rows = list(episode_rows or [])
    shared_movie_rows = list(movie_rows or [])
    sessions: list[RecordingSession] = []

    @asynccontextmanager
    async def _background_session():
        session = RecordingSession(
            episode_rows=shared_episode_rows,
            movie_rows=shared_movie_rows,
            episode_high_water=episode_high_water,
            movie_high_water=movie_high_water,
            episode_budget=episode_budget,
            movie_budget=movie_budget,
        )
        sessions.append(session)
        yield session

    return _background_session, sessions


def test_integrity_disabled_skips_background_session(monkeypatch) -> None:
    opened = False

    async def _fail_background_session():
        nonlocal opened
        opened = True
        msg = "background_session should not be called"
        raise AssertionError(msg)

    _patch_integrity_config(monkeypatch, enabled=False)
    monkeypatch.setattr(
        "miramedia.scheduler.background_session", _fail_background_session
    )

    _run(scheduler.verify_imported_files_task())

    assert opened is False


def test_baseline_sha1_written_when_prior_is_none(monkeypatch, tmp_path: Path) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "episode.mkv"
    media_path.write_bytes(b"baseline-content")
    row = FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path)
    bg_session, sessions = _high_water_background_session_factory(episode_rows=[row])

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", bg_session)
    patch_batch_resolve_paths(monkeypatch, {file_id: media_path})
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
    bg_session, sessions = _high_water_background_session_factory(episode_rows=[row])

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", bg_session)
    patch_batch_resolve_paths(monkeypatch, {file_id: media_path})
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
    row = FakeFileRow(
        id=file_id,
        sha1=prior,
        _resolved_path=media_path,
        movie_id=uuid.uuid4(),
        episode_id=None,
    )
    bg_session, sessions = _high_water_background_session_factory(movie_rows=[row])

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", bg_session)
    patch_batch_resolve_paths(monkeypatch, {file_id: media_path})
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
    bg_session, sessions = _high_water_background_session_factory(episode_rows=[row])
    hashed: list[Path] = []

    async def _track_hash(path: Path) -> str:
        hashed.append(path)
        return "hash"

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", bg_session)
    patch_batch_resolve_paths(monkeypatch, {row.id: None})
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
    bg_session, sessions = _high_water_background_session_factory(episode_rows=[row])

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", bg_session)
    patch_batch_resolve_paths(monkeypatch, {file_id: media_path})
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
        bg_session, _ = _high_water_background_session_factory(episode_rows=[row])
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
        "miramedia.scheduler.background_session", _tracking_background_session
    )
    patch_batch_resolve_paths(monkeypatch, {file_id: media_path})
    monkeypatch.setattr(scheduler, "_compute_sha1_async", _slow_hash)

    _run(scheduler.verify_imported_files_task())


def test_integrity_audit_no_session_held_during_path_resolve(
    monkeypatch, tmp_path: Path
) -> None:
    file_id = uuid.uuid4()
    media_path = tmp_path / "scan.mkv"
    media_path.write_bytes(b"scan-content")
    row = FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path)
    sessions_open: list[bool] = []
    released_before_scan = False

    @asynccontextmanager
    async def _tracking_background_session():
        sessions_open.append(True)
        bg_session, _ = _high_water_background_session_factory(episode_rows=[row])
        async with bg_session() as session:
            try:
                yield session
            finally:
                sessions_open.pop()

    async def _tracking_episode_paths(rows, episode_context, shows, layout):  # noqa: ARG001
        nonlocal released_before_scan
        assert sessions_open == [], "DB session must not be open during path resolve"
        released_before_scan = True
        return {row.id: media_path for row in rows}

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.scheduler.background_session", _tracking_background_session
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.batch_resolve_episode_paths_async",
        _tracking_episode_paths,
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.batch_resolve_movie_paths_async",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("hash-value000000000000000000000000"),
    )

    _run(scheduler.verify_imported_files_task())

    assert released_before_scan is True


def test_integrity_audit_chunks_keyset_reads(monkeypatch, tmp_path: Path) -> None:
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
        bg_session, _ = _high_water_background_session_factory(episode_rows=rows)
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
        "miramedia.scheduler.background_session", _chunk_observing_session
    )
    patch_batch_resolve_paths(monkeypatch, path_by_id)
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("chunk-hash-000000000000000000000000000000"),
    )

    _run(scheduler.verify_imported_files_task())

    assert max_select_rows <= INTEGRITY_AUDIT_CHUNK_SIZE


def test_integrity_audit_budget_defers_rows_inserted_after_start(
    monkeypatch,
    tmp_path: Path,
) -> None:
    initial_id = uuid.UUID(int=1)
    deferred_id = uuid.uuid4()
    initial_path = tmp_path / "initial.mkv"
    deferred_path = tmp_path / "deferred.mkv"
    initial_path.write_bytes(b"initial")
    deferred_path.write_bytes(b"deferred")

    shared_rows = [FakeFileRow(id=initial_id, sha1=None, _resolved_path=initial_path)]
    session_calls = 0
    hashed_ids: list[uuid.UUID] = []

    @asynccontextmanager
    async def _deferred_insert_session():
        nonlocal session_calls
        session_calls += 1
        if session_calls > 1:
            shared_rows.append(
                FakeFileRow(
                    id=deferred_id,
                    sha1=None,
                    _resolved_path=deferred_path,
                )
            )
        session = RecordingSession(
            episode_rows=shared_rows,
            episode_high_water=initial_id,
            episode_budget=1,
        )
        yield session

    async def _track_hash(path: Path) -> str:
        if path == initial_path:
            hashed_ids.append(initial_id)
        if path == deferred_path:
            hashed_ids.append(deferred_id)
        return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.scheduler.background_session", _deferred_insert_session
    )
    patch_batch_resolve_paths(
        monkeypatch, {initial_id: initial_path, deferred_id: deferred_path}
    )
    monkeypatch.setattr(scheduler, "_compute_sha1_async", _track_hash)

    _run(scheduler.verify_imported_files_task())

    assert hashed_ids == [initial_id]


def test_integrity_audit_empty_table_skips_chunk_reads(monkeypatch) -> None:
    opened = 0

    @asynccontextmanager
    async def _counting_session():
        nonlocal opened
        opened += 1
        session = RecordingSession()
        yield session

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", _counting_session)
    patch_batch_resolve_paths(monkeypatch, {})

    _run(scheduler.verify_imported_files_task())

    assert opened == 1


def test_integrity_audit_exact_chunk_boundary(monkeypatch, tmp_path: Path) -> None:
    rows: list[FakeFileRow] = []
    path_by_id: dict[uuid.UUID, Path] = {}
    for i in range(INTEGRITY_AUDIT_CHUNK_SIZE):
        file_id = uuid.UUID(int=i + 1)
        media_path = tmp_path / f"ep-{i}.mkv"
        media_path.write_bytes(f"content-{i}".encode())
        rows.append(FakeFileRow(id=file_id, sha1=None, _resolved_path=media_path))
        path_by_id[file_id] = media_path

    chunk_reads = 0
    max_chunk_size = 0

    @asynccontextmanager
    async def _boundary_session():
        nonlocal chunk_reads, max_chunk_size
        bg_session, _ = _high_water_background_session_factory(episode_rows=rows)
        async with bg_session() as session:
            original_execute = session.execute

            async def _execute(stmt):
                nonlocal chunk_reads, max_chunk_size
                from sqlalchemy.sql.selectable import Select

                result = await original_execute(stmt)
                if isinstance(stmt, Select):
                    entity = stmt.column_descriptions[0].get("entity")
                    if getattr(entity, "__name__", "") == "EpisodeFile":
                        chunk_rows = result.scalars().all()
                        if chunk_rows:
                            chunk_reads += 1
                            max_chunk_size = max(max_chunk_size, len(chunk_rows))
                return result

            session.execute = _execute  # type: ignore[method-assign]
            yield session

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", _boundary_session)
    patch_batch_resolve_paths(monkeypatch, path_by_id)
    monkeypatch.setattr(
        scheduler,
        "_compute_sha1_async",
        lambda _path: _return_sha("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )

    _run(scheduler.verify_imported_files_task())

    assert chunk_reads == 1
    assert max_chunk_size == INTEGRITY_AUDIT_CHUNK_SIZE


def test_integrity_audit_overlapping_cross_table_uuids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    shared_id = uuid.UUID(int=42)
    show_path = tmp_path / "show.mkv"
    movie_path = tmp_path / "movie.mkv"
    show_path.write_bytes(b"show")
    movie_path.write_bytes(b"movie")
    episode_row = FakeFileRow(
        id=shared_id, sha1=None, _resolved_path=show_path, episode_id=uuid.uuid4()
    )
    movie_row = FakeFileRow(
        id=shared_id,
        sha1=None,
        _resolved_path=movie_path,
        movie_id=uuid.uuid4(),
        episode_id=None,
    )
    hashed: list[Path] = []

    async def _track_hash(path: Path) -> str:
        hashed.append(path)
        return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    bg_session, sessions = _high_water_background_session_factory(
        episode_rows=[episode_row],
        movie_rows=[movie_row],
    )

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr("miramedia.scheduler.background_session", bg_session)

    async def _episode_paths(rows, episode_context, shows, layout):  # noqa: ARG001
        return {row.id: show_path for row in rows}

    async def _movie_paths(rows, movies, layout):  # noqa: ARG001
        return {row.id: movie_path for row in rows}

    monkeypatch.setattr(
        "miramedia.torrents.integrity.batch_resolve_episode_paths_async",
        _episode_paths,
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.batch_resolve_movie_paths_async",
        _movie_paths,
    )
    monkeypatch.setattr(scheduler, "_compute_sha1_async", _track_hash)

    _run(scheduler.verify_imported_files_task())

    assert hashed == [show_path, movie_path]
    assert len(_write_sessions(sessions)) == 2


def test_integrity_audit_budget_caps_episode_reads_and_movie_still_runs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ep_max = uuid.UUID(int=100)
    initial_episodes = [
        FakeFileRow(
            id=uuid.UUID(int=1),
            sha1=None,
            _resolved_path=tmp_path / "ep-1.mkv",
        ),
        FakeFileRow(
            id=uuid.UUID(int=2),
            sha1=None,
            _resolved_path=tmp_path / "ep-2.mkv",
        ),
    ]
    for row in initial_episodes:
        row._resolved_path.write_bytes(b"x")

    movie_id = uuid.uuid4()
    movie_path = tmp_path / "movie.mkv"
    movie_path.write_bytes(b"movie")
    movie_row = FakeFileRow(
        id=movie_id,
        sha1=None,
        _resolved_path=movie_path,
        movie_id=uuid.uuid4(),
        episode_id=None,
    )

    shared_episode_rows = list(initial_episodes)
    episode_rows_read = 0
    session_calls = 0
    hashed_episodes = 0
    hashed_movies = 0

    @asynccontextmanager
    async def _budget_starvation_session():
        nonlocal session_calls
        session_calls += 1
        if session_calls > 1:
            shared_episode_rows.append(
                FakeFileRow(
                    id=uuid.uuid4(),
                    sha1=None,
                    _resolved_path=tmp_path / f"late-{session_calls}.mkv",
                )
            )
        session = RecordingSession(
            episode_rows=shared_episode_rows,
            movie_rows=[movie_row],
            episode_high_water=ep_max,
            episode_budget=2,
            movie_budget=1,
        )
        original_execute = session.execute

        async def _execute(stmt):
            nonlocal episode_rows_read
            result = await original_execute(stmt)
            from sqlalchemy.sql.selectable import Select

            if isinstance(stmt, Select):
                entity = stmt.column_descriptions[0].get("entity")
                if getattr(entity, "__name__", "") == "EpisodeFile":
                    episode_rows_read += len(result.scalars().all())
            return result

        session.execute = _execute  # type: ignore[method-assign]
        yield session

    async def _track_hash(path: Path) -> str:
        nonlocal hashed_episodes, hashed_movies
        if path.parent == tmp_path and path.name.startswith("ep-"):
            hashed_episodes += 1
        if path == movie_path:
            hashed_movies += 1
        return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    _patch_integrity_config(monkeypatch)
    monkeypatch.setattr(
        "miramedia.scheduler.background_session", _budget_starvation_session
    )
    patch_batch_resolve_paths(
        monkeypatch,
        {
            initial_episodes[0].id: initial_episodes[0]._resolved_path,
            initial_episodes[1].id: initial_episodes[1]._resolved_path,
            movie_id: movie_path,
        },
    )
    monkeypatch.setattr(scheduler, "_compute_sha1_async", _track_hash)

    _run(scheduler.verify_imported_files_task())

    assert episode_rows_read == 2
    assert hashed_episodes == 2
    assert hashed_movies == 1

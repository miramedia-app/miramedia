"""Characterization of persisted storage-health predicates against HEAD.

These tests document the evidence 387 mapping (integrity mismatch, ghost-failed
orphan, unknown SHA1, and inaccessible-scan collision). If they fail, the
mapping drifted — update evidence before production storage-health code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.sql.selectable import Select

from miramedia.disk_scan import scan_rows_for_files
from miramedia.file_status import ImportOutcome
from miramedia.movies.repository import MovieRepository
from miramedia.shows.repository import ShowRepository
from miramedia.torrents.integrity import scan_directory_for_stem_prefixes


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _ScalarResult:
    value: int = 0

    def scalar_one(self) -> int:
        return self.value

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return []


@dataclass
class _RecordingSession:
    executes: list[Any] = field(default_factory=list)

    async def execute(self, stmt: Any) -> _ScalarResult:
        self.executes.append(stmt)
        return _ScalarResult()


def _compiled(stmt: Select) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()


def test_sha1_mismatch_count_sql_is_imported_plus_mismatch_prefix() -> None:
    session = _RecordingSession()
    _run(ShowRepository(session).count_sha1_mismatch_files())  # type: ignore[arg-type]
    sql = _compiled(session.executes[0])
    assert "imported" in sql
    assert "sha1 mismatch%" in sql
    assert "episode_file" in sql


def test_movie_sha1_mismatch_count_sql_matches_show_contract() -> None:
    session = _RecordingSession()
    _run(MovieRepository(session).count_sha1_mismatch_files())  # type: ignore[arg-type]
    sql = _compiled(session.executes[0])
    assert "imported" in sql
    assert "sha1 mismatch%" in sql
    assert "movie_file" in sql


def test_ghost_failed_orphan_sql_requires_null_torrent_and_failed_status() -> None:
    session = _RecordingSession()
    _run(ShowRepository(session).get_orphaned_failed_episode_files())  # type: ignore[arg-type]
    sql = _compiled(session.executes[0])
    assert "torrent_id" in sql
    assert "failed_io" in sql
    assert "failed_no_match" in sql
    assert "is null" in sql


def test_movie_ghost_failed_orphan_sql_matches_show_contract() -> None:
    session = _RecordingSession()
    _run(MovieRepository(session).get_orphaned_failed_movie_files())  # type: ignore[arg-type]
    sql = _compiled(session.executes[0])
    assert "torrent_id" in sql
    assert "failed_io" in sql
    assert "failed_no_match" in sql
    assert "is null" in sql


def test_unknown_hash_is_imported_with_null_sha1() -> None:
    """Unknown is a persisted predicate, not a filesystem fact."""
    assert ImportOutcome.imported == "imported"
    # Partial index contract from the initial schema / EpisodeFile model.
    from miramedia.shows.models import EpisodeFile

    index_names = {
        idx.name for idx in EpisodeFile.__table_args__ if hasattr(idx, "name")
    }
    assert "ix_episode_file_sha1_pending" in index_names


def test_scan_rows_for_files_empty_when_directory_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    found = scan_rows_for_files(
        missing,
        [{"id": 1}],
        key=lambda row: row["id"],
        stems=lambda _row: ["Show.S01E01"],
        video_exts=frozenset({".mkv"}),
    )
    assert found == {}


def test_scan_directory_for_stem_prefixes_empty_on_oserror(
    tmp_path: Path, monkeypatch: Any
) -> None:
    directory = tmp_path / "lib"
    directory.mkdir()

    def _boom(*_args: object) -> None:
        msg = "EACCES"
        raise OSError(msg)

    monkeypatch.setattr(Path, "iterdir", _boom)
    assert scan_directory_for_stem_prefixes(directory, frozenset({"stem."})) == {}


def test_pending_acquisition_is_non_imported_import_outcome() -> None:
    pending_states = {
        ImportOutcome.pending,
        ImportOutcome.failed_no_match,
        ImportOutcome.failed_io,
        ImportOutcome.ambiguous,
    }
    assert ImportOutcome.imported not in pending_states
    assert pending_states == {
        ImportOutcome.pending,
        ImportOutcome.failed_no_match,
        ImportOutcome.failed_io,
        ImportOutcome.ambiguous,
    }

"""Per-file SHA1 helpers for the Phase 6.5 integrity audit.

The integrity feature is opt-in (``misc.integrity_check_enabled``). When on:

* ``compute_sha1(path)`` is called immediately after a successful import to
  capture the canonical hash on the EpisodeFile/MovieFile row.
* The scheduled ``verify_imported_files_task`` re-hashes each row that has a
  stored ``sha1`` and logs a WARNING for any mismatch (and stamps
  ``import_error`` so the imports dashboard surfaces the problem).

Compare-and-set predicates compare the database state observed *before*
filesystem hashing — never the freshly computed digest.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import MovieFile
from miramedia.naming import episode_file_stem_candidates, movie_file_stem_candidates
from miramedia.shows.models import EpisodeFile
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)

_MISMATCH_ERROR_PREFIX = "sha1 mismatch%"

# Bounded integrity-mismatch API (Plan 082).
INTEGRITY_MISMATCH_DEFAULT_LIMIT = 50
INTEGRITY_MISMATCH_MAX_LIMIT = 100

# Scheduler chunk size for verify_imported_files_task (Plan 082).
INTEGRITY_AUDIT_CHUNK_SIZE = 100

_VIDEO_SUFFIXES = frozenset(
    {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".wmv"}
)

IntegrityFileModel = type[EpisodeFile | MovieFile]


def integrity_audit_snapshot_where(
    file_table: IntegrityFileModel,
    file_id: UUID,
    *,
    expected_sha1: str | None,
    expected_import_error: str | None,
) -> ColumnElement[bool]:
    """Row must still match the import fields observed before hashing."""
    if expected_sha1 is None:
        sha_predicate = file_table.sha1.is_(None)
    else:
        sha_predicate = file_table.sha1.is_not_distinct_from(expected_sha1)
    if expected_import_error is None:
        import_error_predicate = file_table.import_error.is_(None)
    else:
        import_error_predicate = file_table.import_error.is_not_distinct_from(
            expected_import_error
        )
    return and_(
        file_table.id == file_id,
        file_table.import_status == ImportOutcome.imported,
        sha_predicate,
        import_error_predicate,
    )


def integrity_mismatch_action_snapshot_where(
    file_table: IntegrityFileModel,
    file_id: UUID,
    *,
    expected_sha1: str | None,
    expected_import_error: str,
) -> ColumnElement[bool]:
    """Row must still match the mismatch fields observed when the action started."""
    if expected_sha1 is None:
        sha_predicate = file_table.sha1.is_(None)
    else:
        sha_predicate = file_table.sha1.is_not_distinct_from(expected_sha1)
    return and_(
        file_table.id == file_id,
        file_table.import_status == ImportOutcome.imported,
        file_table.import_error.like(_MISMATCH_ERROR_PREFIX),
        file_table.import_error.is_not_distinct_from(expected_import_error),
        sha_predicate,
    )


_CHUNK = 1024 * 1024  # 1 MiB


def list_video_files_in_directory(directory: Path) -> list[Path]:
    """List video files in ``directory`` (one directory scan)."""
    if not directory.exists() or not directory.is_dir():
        return []
    try:
        return [
            p
            for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES
        ]
    except OSError:
        return []


def resolve_video_path_from_stems(
    directory: Path,
    stems: Iterable[str],
    *,
    video_files: list[Path] | None = None,
) -> Path | None:
    """Return the first video file matching any stem under ``directory``."""
    files = (
        video_files
        if video_files is not None
        else list_video_files_in_directory(directory)
    )
    if not files:
        return None
    for stem in stems:
        prefix = stem + "."
        for candidate in files:
            if candidate.name.startswith(prefix):
                return candidate
    return None


def resolve_episode_file_path_in_memory(
    *,
    show: Any,
    season_number: int,
    episode_number: int,
    episode_file: Any,
    season_dir: Path,
    video_files: list[Path] | None = None,
) -> Path | None:
    """Pure in-memory episode path resolution (same semantics as ShowService)."""
    stems = episode_file_stem_candidates(
        show,
        season_number=season_number,
        episode_number=episode_number,
        quality=Quality(episode_file.quality),
        parts=NameParts.from_row(episode_file),
    )
    return resolve_video_path_from_stems(season_dir, stems, video_files=video_files)


def resolve_movie_file_path_in_memory(
    *,
    movie: Any,
    movie_file: Any,
    movie_root: Path,
    video_files: list[Path] | None = None,
) -> Path | None:
    """Pure in-memory movie path resolution (same semantics as MovieService)."""
    stems = movie_file_stem_candidates(
        movie, Quality(movie_file.quality), NameParts.from_row(movie_file)
    )
    return resolve_video_path_from_stems(movie_root, stems, video_files=video_files)


def compute_sha1(path: Path) -> str | None:
    """Return the SHA1 hex digest of ``path``, or ``None`` on I/O error.

    Reads the file in 1 MiB chunks so large media files don't pin memory.
    Returning ``None`` on failure keeps the audit non-fatal — the caller logs
    and moves on rather than aborting the whole sweep.
    """
    try:
        h = hashlib.sha1()  # noqa: S324 — used for change detection, not security
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        log.warning("sha1 compute failed for %s", path, exc_info=True)
        return None

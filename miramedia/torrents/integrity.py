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
from pathlib import Path
from uuid import UUID

from sqlalchemy import ColumnElement, and_

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import MovieFile
from miramedia.shows.models import EpisodeFile

log = logging.getLogger(__name__)

_MISMATCH_ERROR_PREFIX = "sha1 mismatch%"

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


def integrity_mismatch_action_where(
    file_table: IntegrityFileModel,
    file_id: UUID,
) -> ColumnElement[bool]:
    """Row must still be an imported file with an active mismatch stamp."""
    return and_(
        file_table.id == file_id,
        file_table.import_status == ImportOutcome.imported,
        file_table.import_error.like(_MISMATCH_ERROR_PREFIX),
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

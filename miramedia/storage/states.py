"""SQL and overlay classification for the read-only storage-health surface."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import ColumnElement, and_, not_, or_

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import MovieFile
from miramedia.shows.models import EpisodeFile

SHA1_MISMATCH_LIKE = "sha1 mismatch%"
SHA1_MISMATCH_PREFIX = "sha1 mismatch"

SqlHealthState = Literal["corrupt", "unknown", "orphaned", "pending", "healthy"]
DisplayedHealthState = Literal[
    "corrupt",
    "unknown",
    "orphaned",
    "pending",
    "healthy",
    "missing",
    "inaccessible",
]
ListFilterState = SqlHealthState

STATE_RANK: dict[SqlHealthState, int] = {
    "corrupt": 0,
    "orphaned": 1,
    "pending": 2,
    "unknown": 3,
    "healthy": 4,
}

GHOST_FAILED = (ImportOutcome.failed_io, ImportOutcome.failed_no_match)

_IMPORTED_SQL_STATES: frozenset[SqlHealthState] = frozenset(
    {"corrupt", "unknown", "healthy"}
)

IntegrityFileModel = type[EpisodeFile | MovieFile]


def is_mismatch_error(import_error: str | None) -> bool:
    return (import_error or "").startswith(SHA1_MISMATCH_PREFIX)


def classify_sql_state(
    *,
    import_status: ImportOutcome,
    import_error: str | None,
    sha1: str | None,
    torrent_id: UUID | str | None,
) -> SqlHealthState:
    """First-match SQL class (no filesystem overlay)."""
    if import_status == ImportOutcome.imported:
        if is_mismatch_error(import_error):
            return "corrupt"
        if sha1 is None:
            return "unknown"
        return "healthy"
    if torrent_id is None and import_status in GHOST_FAILED:
        return "orphaned"
    return "pending"


def apply_path_overlay(
    sql_state: SqlHealthState,
    *,
    library_ok: bool | None,
    path: str | None,
) -> DisplayedHealthState:
    """Apply I6/I7 overlay: inaccessible wins; missing only after a live root."""
    if library_ok is False:
        return "inaccessible"
    if library_ok is True and path is None and sql_state in _IMPORTED_SQL_STATES:
        return "missing"
    return sql_state


def imported_clause(table: IntegrityFileModel) -> ColumnElement[bool]:
    return table.import_status == ImportOutcome.imported


def corrupt_clause(table: IntegrityFileModel) -> ColumnElement[bool]:
    return and_(
        imported_clause(table),
        table.import_error.like(SHA1_MISMATCH_LIKE),
    )


def unknown_clause(table: IntegrityFileModel) -> ColumnElement[bool]:
    return and_(
        imported_clause(table),
        table.sha1.is_(None),
        or_(
            table.import_error.is_(None),
            not_(table.import_error.like(SHA1_MISMATCH_LIKE)),
        ),
    )


def healthy_clause(table: IntegrityFileModel) -> ColumnElement[bool]:
    return and_(
        imported_clause(table),
        table.sha1.is_not(None),
        or_(
            table.import_error.is_(None),
            not_(table.import_error.like(SHA1_MISMATCH_LIKE)),
        ),
    )


def orphaned_clause(table: IntegrityFileModel) -> ColumnElement[bool]:
    return and_(
        table.torrent_id.is_(None),
        table.import_status.in_(GHOST_FAILED),
    )


def pending_clause(table: IntegrityFileModel) -> ColumnElement[bool]:
    return and_(
        table.import_status != ImportOutcome.imported,
        not_(orphaned_clause(table)),
    )

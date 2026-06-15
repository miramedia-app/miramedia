from enum import StrEnum


class FileStatus(StrEnum):
    imported = "imported"
    queued = "queued"
    removed = "removed"
    orphaned = "orphaned"


class ImportOutcome(StrEnum):
    """Persisted state of the import attempt for an EpisodeFile / MovieFile.

    Distinct from :class:`FileStatus`, which is derived from on-disk presence
    at request time. ``ImportOutcome`` records the most recent import attempt
    and lets the UI surface stuck/failed torrents without scanning disk.
    """

    pending = "pending"
    imported = "imported"
    failed_no_match = "failed_no_match"
    failed_io = "failed_io"
    ambiguous = "ambiguous"

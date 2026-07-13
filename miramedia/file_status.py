"""On-disk and import-attempt status vocabularies for media files.

Four status enums cover distinct facts on public episode/movie rows:

- :class:`~miramedia.media_status.MediaStatus` — user intent (wanted /
  downloaded / skipped).
- :class:`FileStatus` — on-disk truth at request time (imported / queued /
  removed / orphaned).
- :class:`ImportOutcome` — last import attempt (pending / imported /
  failed_* / ambiguous).
- :class:`~miramedia.media_state.ProgressStatus` — derived list-progress
  display for shows (none / partial / complete).
"""

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

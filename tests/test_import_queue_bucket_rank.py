"""``_bucket_rank`` orders the ``all`` imports tab so action-needed rows
(Review=0, Retry=1) sort ahead of Done=2.

This keeps reviewable scan + torrent items grouped on the first page instead of
scattering behind chronologically-newer Done rows. The ranks must mirror the
frontend ``bucketOf`` grouping in ``web/.../imports/page.tsx``.
"""

from __future__ import annotations

import pytest

from miramedia.imports.queue.sync import _bucket_rank
from miramedia.imports.schemas import ScanImportItem, ScanResult, TorrentImportItem
from miramedia.torrents.schemas import (
    ImportProgress,
    ImportStatusEntry,
    TorrentId,
    TorrentStatus,
)


def _torrent(progress: ImportProgress, backoff: int | None = None) -> TorrentImportItem:
    entry = ImportStatusEntry(
        torrent_id=TorrentId("11111111-1111-1111-1111-111111111111"),
        torrent_title="Some Release",
        torrent_status=TorrentStatus.finished,
        progress=progress,
        files=[],
    )
    return TorrentImportItem(id="t1", entry=entry, backoff_seconds=backoff)


def _scan(status: str) -> ScanImportItem:
    return ScanImportItem(
        id="/data/Some Dir",
        result=ScanResult(
            directory="/data/Some Dir",
            detected_name="Some Dir",
            library_name="movies",
            status=status,
        ),
    )


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        # Torrent with a failed/ambiguous file → Review.
        (_torrent(ImportProgress(total=2, failed=1)), 0),
        (_torrent(ImportProgress(total=2, ambiguous=1)), 0),
        # In-progress / partial torrent (no failures) → Review, matching the UI.
        (_torrent(ImportProgress(total=2, imported=1, pending=1)), 0),
        # Awaiting auto-retry backoff → Retry.
        (_torrent(ImportProgress(total=2, imported=1, pending=1), backoff=120), 1),
        # Fully imported torrent → Done.
        (_torrent(ImportProgress(total=2, imported=2)), 2),
        # Scans: pending needs a human pick (Review); imported is Done.
        (_scan("pending"), 0),
        (_scan("failed"), 0),
        (_scan("imported"), 2),
    ],
)
def test_bucket_rank(item: object, expected: int) -> None:
    assert _bucket_rank(item) == expected  # type: ignore[arg-type]


def test_review_outranks_done() -> None:
    review = _bucket_rank(_scan("pending"))
    done = _bucket_rank(_torrent(ImportProgress(total=1, imported=1)))
    assert review < done

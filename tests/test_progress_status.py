"""Unit tests for media_state._progress_status."""

from miramedia.media_state import ProgressStatus, _progress_status
from miramedia.media_status import MediaStatus
from miramedia.torrents.schemas import TorrentHistoryOutcome


def test_status_enum_members_match_wire_format() -> None:
    assert ProgressStatus.none == "none"
    assert TorrentHistoryOutcome.downloaded == "downloaded"
    assert MediaStatus.downloaded == "downloaded"


def test_progress_status_none_when_nothing_downloaded() -> None:
    assert _progress_status(0, 0) == ProgressStatus.none
    assert _progress_status(5, 0) == ProgressStatus.none


def test_progress_status_partial_when_some_downloaded() -> None:
    assert _progress_status(5, 1) == ProgressStatus.partial
    assert _progress_status(5, 4) == ProgressStatus.partial


def test_progress_status_complete_when_all_wanted_downloaded() -> None:
    assert _progress_status(3, 3) == ProgressStatus.complete

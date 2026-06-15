"""The imports page only surfaces torrents whose download has finished.

``TorrentService.is_import_ready`` is the single gate shared by the full queue
rebuild and the per-torrent sync. Linking happens at download *start*, so this
gate is what keeps in-progress downloads off the imports page.
"""

import types

import pytest

from miramedia.torrents.schemas import TorrentStatus
from miramedia.torrents.service import TorrentService


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TorrentStatus.finished, True),
        (TorrentStatus.downloading, False),
        (TorrentStatus.paused, False),
        (TorrentStatus.error, False),
        (TorrentStatus.unknown, False),
    ],
)
def test_is_import_ready(status: TorrentStatus, expected: bool) -> None:
    torrent = types.SimpleNamespace(status=status)
    assert TorrentService.is_import_ready(torrent) is expected

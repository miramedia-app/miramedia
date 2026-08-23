"""Tests for _parse_torrent_bytes — libtorrent-based bdecode helper.

Fixtures are hand-crafted bencoded .torrent payloads with deterministic
content so the expected info-hash can be independently verified as
sha1(info_dict_bytes).

Single-file expected hash:  1f9254e87da3f6f6daf88104bb2e5b1c47355466
Multi-file expected hash:   32d3037ea0b4ab2a519b0989015cb6879aea77ad

Both were proven equivalent to the old bencoder code in the equivalence
harness (Step 1 of plan 014).
"""

import hashlib

from miramedia.torrents.inspection import (
    TorrentInspection,
    _parse_torrent_bytes,
    inspect_torrent,
)

# ---------------------------------------------------------------------------
# Deterministic bencoding helpers (no external dep)
# ---------------------------------------------------------------------------


def _be_str(s: bytes) -> bytes:
    return str(len(s)).encode() + b":" + s


def _be_int(n: int) -> bytes:
    return b"i" + str(n).encode() + b"e"


def _be_dict(d: dict) -> bytes:
    result = b"d"
    for k in sorted(d.keys()):
        result += _be_str(k) + d[k]
    result += b"e"
    return result


def _be_list(lst: list) -> bytes:
    result = b"l"
    for item in lst:
        result += item
    result += b"e"
    return result


# ---------------------------------------------------------------------------
# Frozen fixtures
# ---------------------------------------------------------------------------

# Single-file torrent: info dict with name=testfile.mkv, length=1024
_SINGLE_INFO_BYTES = _be_dict(
    {
        b"length": _be_int(1024),
        b"name": _be_str(b"testfile.mkv"),
        b"piece length": _be_int(262144),
        b"pieces": _be_str(b"\x00" * 20),
    }
)
SINGLE_TORRENT_BYTES = _be_dict({b"info": _SINGLE_INFO_BYTES})
SINGLE_EXPECTED_HASH = "1f9254e87da3f6f6daf88104bb2e5b1c47355466"

# Verify our hand-built expectation is the sha1 of the raw info bytes
assert hashlib.sha1(_SINGLE_INFO_BYTES).hexdigest().lower() == SINGLE_EXPECTED_HASH, (
    "Fixture self-check failed"
)

# Multi-file torrent: name=TestShow with sub/file1.mkv (512B) and file2.srt (1024B)
_MULTI_INFO_BYTES = _be_dict(
    {
        b"files": _be_list(
            [
                _be_dict(
                    {
                        b"length": _be_int(512),
                        b"path": _be_list([_be_str(b"sub"), _be_str(b"file1.mkv")]),
                    }
                ),
                _be_dict(
                    {
                        b"length": _be_int(1024),
                        b"path": _be_list([_be_str(b"file2.srt")]),
                    }
                ),
            ]
        ),
        b"name": _be_str(b"TestShow"),
        b"piece length": _be_int(262144),
        b"pieces": _be_str(b"\x00" * 20),
    }
)
MULTI_TORRENT_BYTES = _be_dict({b"info": _MULTI_INFO_BYTES})
MULTI_EXPECTED_HASH = "32d3037ea0b4ab2a519b0989015cb6879aea77ad"

assert hashlib.sha1(_MULTI_INFO_BYTES).hexdigest().lower() == MULTI_EXPECTED_HASH, (
    "Fixture self-check failed"
)


# ---------------------------------------------------------------------------
# _parse_torrent_bytes tests
# ---------------------------------------------------------------------------


class TestParseTorrentBytes:
    def test_single_file_info_hash(self):
        """Info-hash must match sha1(info_dict_bytes) — frozen."""
        result = _parse_torrent_bytes(SINGLE_TORRENT_BYTES)
        assert result is not None
        info_hash, _name, _files = result
        assert info_hash == SINGLE_EXPECTED_HASH

    def test_single_file_hash_is_lowercase(self):
        result = _parse_torrent_bytes(SINGLE_TORRENT_BYTES)
        assert result is not None
        info_hash, _, _ = result
        assert info_hash == info_hash.lower()
        assert len(info_hash) == 40

    def test_single_file_name(self):
        result = _parse_torrent_bytes(SINGLE_TORRENT_BYTES)
        assert result is not None
        _hash, name, _files = result
        assert name == "testfile.mkv"

    def test_single_file_file_list(self):
        """Single-file torrent yields one entry with the file name and correct size."""
        result = _parse_torrent_bytes(SINGLE_TORRENT_BYTES)
        assert result is not None
        _hash, _name, files = result
        assert len(files) == 1
        path_str, size = files[0]
        assert path_str == "testfile.mkv"
        assert size == 1024

    def test_multi_file_info_hash(self):
        """Info-hash must match sha1(info_dict_bytes) — frozen."""
        result = _parse_torrent_bytes(MULTI_TORRENT_BYTES)
        assert result is not None
        info_hash, _name, _files = result
        assert info_hash == MULTI_EXPECTED_HASH

    def test_multi_file_name(self):
        result = _parse_torrent_bytes(MULTI_TORRENT_BYTES)
        assert result is not None
        _hash, name, _files = result
        assert name == "TestShow"

    def test_multi_file_list_paths_and_sizes(self):
        """Multi-file: paths strip the torrent-name prefix; sizes are correct."""
        result = _parse_torrent_bytes(MULTI_TORRENT_BYTES)
        assert result is not None
        _hash, _name, files = result
        assert len(files) == 2
        paths = dict(files)
        assert "sub/file1.mkv" in paths
        assert paths["sub/file1.mkv"] == 512
        assert "file2.srt" in paths
        assert paths["file2.srt"] == 1024

    def test_multi_file_no_torrent_name_prefix_in_paths(self):
        """Paths must NOT be prefixed with the torrent name."""
        result = _parse_torrent_bytes(MULTI_TORRENT_BYTES)
        assert result is not None
        _hash, name, files = result
        for fp, _ in files:
            assert not fp.startswith(name + "/"), (
                f"Path {fp!r} still has torrent-name prefix"
            )

    def test_malformed_bytes_returns_none(self):
        """Garbage bytes must return None (not raise)."""
        assert _parse_torrent_bytes(b"not a torrent") is None
        assert _parse_torrent_bytes(b"") is None
        assert _parse_torrent_bytes(b"d3:foo3:bare") is None

    def test_truncated_bytes_returns_none(self):
        """Truncated payload must return None."""
        assert _parse_torrent_bytes(SINGLE_TORRENT_BYTES[:10]) is None


# ---------------------------------------------------------------------------
# Integration: inspect_torrent with _parse_torrent_bytes under the hood
# ---------------------------------------------------------------------------


class TestInspectTorrentIntegration:
    """Smoke-test that inspect_torrent routes through _parse_torrent_bytes
    correctly by mocking out the network fetch."""

    def test_inspect_torrent_returns_correct_hash_and_files(self, monkeypatch):
        from miramedia.indexers.schemas import IndexerQueryResult

        monkeypatch.setattr(
            "miramedia.torrents.inspection._fetch_torrent_payload",
            lambda *_args: MULTI_TORRENT_BYTES,
        )

        result_obj = IndexerQueryResult(
            title="TestShow",
            download_url="http://fake.example.com/TestShow.torrent",
            seeders=0,
            size=0,
            flags=[],
            usenet=False,
            age=0,
            indexer=None,
        )
        inspection = inspect_torrent(result_obj)

        assert isinstance(inspection, TorrentInspection)
        assert inspection.info_hash == MULTI_EXPECTED_HASH
        assert inspection.files is not None
        assert len(inspection.files) == 2

        paths = {str(f.path): f.size for f in inspection.files}
        assert "sub/file1.mkv" in paths
        assert paths["sub/file1.mkv"] == 512
        assert "file2.srt" in paths
        assert paths["file2.srt"] == 1024

    def test_inspect_torrent_malformed_returns_none_fields(self, monkeypatch):
        from miramedia.indexers.schemas import IndexerQueryResult

        monkeypatch.setattr(
            "miramedia.torrents.inspection._fetch_torrent_payload",
            lambda *_args: b"garbage",
        )

        result_obj = IndexerQueryResult(
            title="Bad",
            download_url="http://fake.example.com/bad.torrent",
            seeders=0,
            size=0,
            flags=[],
            usenet=False,
            age=0,
            indexer=None,
        )
        inspection = inspect_torrent(result_obj)
        assert inspection.info_hash is None
        assert inspection.files is None

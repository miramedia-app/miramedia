"""DB-free tests for batched torrent deny-list lookups."""

from __future__ import annotations

import asyncio

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.service import TorrentService
from tests.fakes.repositories import FakeTorrentRepository

_HASH_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_HASH_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_HASH_C = "cccccccccccccccccccccccccccccccccccccccc"


def _magnet(info_hash: str) -> str:
    return f"magnet:?xt=urn:btih:{info_hash}"


def _result(download_url: str, *, title: str = "release") -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=download_url,
        seeders=10,
        flags=[],
        size=1_000_000,
        usenet=False,
        age=1,
        indexer="test",
    )


def test_get_blocked_hashes_returns_only_listed() -> None:
    torrent_repo = FakeTorrentRepository()
    torrent_repo.blocked_hashes = {_HASH_A, _HASH_C}

    blocked = asyncio.run(torrent_repo.get_blocked_hashes([_HASH_A, _HASH_B, _HASH_C]))

    assert blocked == {_HASH_A, _HASH_C}


def test_get_blocked_hashes_is_case_insensitive() -> None:
    torrent_repo = FakeTorrentRepository()
    torrent_repo.blocked_hashes = {_HASH_A}

    assert asyncio.run(torrent_repo.get_blocked_hashes([_HASH_A.upper()])) == {_HASH_A}
    assert asyncio.run(torrent_repo.is_hash_blocked(_HASH_A.upper())) is True


def test_get_blocked_hashes_empty_input_skips_lookup() -> None:
    torrent_repo = FakeTorrentRepository()
    torrent_repo.blocked_hashes = {_HASH_A}

    assert asyncio.run(torrent_repo.get_blocked_hashes([])) == set()
    assert asyncio.run(torrent_repo.get_blocked_hashes(["", "   "])) == set()
    assert getattr(torrent_repo, "get_blocked_hashes_calls", 0) == 0


def test_filter_deny_listed_calls_repository_once() -> None:
    torrent_repo = FakeTorrentRepository()
    torrent_repo.blocked_hashes = {_HASH_B}
    svc = TorrentService(torrent_repository=torrent_repo)  # type: ignore[arg-type]
    results = [
        _result(_magnet(_HASH_A), title="clean-a"),
        _result(_magnet(_HASH_B), title="blocked"),
        _result(_magnet(_HASH_C), title="clean-c"),
    ]

    filtered = asyncio.run(svc.filter_deny_listed(results))

    assert [r.title for r in filtered] == ["clean-a", "clean-c"]
    assert torrent_repo.get_blocked_hashes_calls == 1

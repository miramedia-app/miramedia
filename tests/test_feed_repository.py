"""DB-free tests for feed observation bulk lookup/insert (plan 441)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from miramedia.feeds.repository import (
    _IDENTITY_LOOKUP_CHUNK_SIZE,
    FeedItemIdentity,
    FeedObservationInsert,
    FeedRepository,
    feed_item_identity,
)
from miramedia.feeds.schemas import FeedDecision, FeedEnvelope
from miramedia.indexers.schemas import IndexerQueryResult


def _run(coro):
    return asyncio.run(coro)


def _envelope(
    *,
    guid: str | None = "guid-1",
    info_hash: str | None = None,
    download_url: str = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
    title: str = "Release 2020 1080p",
) -> FeedEnvelope:
    return FeedEnvelope(
        result=IndexerQueryResult(
            title=title,
            download_url=download_url,
            seeders=5,
            flags=[],
            size=1_000_000,
            usenet=False,
            age=1,
            indexer="test-indexer",
        ),
        provider_guid=guid,
        pub_date=datetime.now(UTC),
        info_hash=info_hash,
    )


def _observation(
    envelope: FeedEnvelope,
    *,
    redacted_url: str = "magnet:?xt=urn:btih:<redacted>",
) -> FeedObservationInsert:
    return FeedObservationInsert(
        envelope=envelope,
        download_url_redacted=redacted_url,
        bound_media_type=None,
        bound_media_id=None,
        decision=FeedDecision.unmatched,
        score=None,
    )


class _ScalarResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> _ScalarResult:
        return self

    def __iter__(self):
        return iter(self._values)


class _CountingSession:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.lookup_results: dict[str, list[str]] = {
            "guid": [],
            "info_hash": [],
            "url": [],
        }
        self.insert_return_rows: list[tuple[str | None, str | None, str | None]] = []

    async def execute(self, stmt) -> SimpleNamespace:
        self.execute_calls += 1
        compiled = str(stmt)
        if "RETURNING" in compiled:
            return SimpleNamespace(all=lambda: self.insert_return_rows)
        if "feed_item.provider_guid" in compiled:
            return _ScalarResult(self.lookup_results["guid"])
        if "feed_item.info_hash" in compiled:
            return _ScalarResult(self.lookup_results["info_hash"])
        if "feed_item.download_url_redacted" in compiled:
            return _ScalarResult(self.lookup_results["url"])
        msg = f"unexpected statement: {compiled}"
        raise AssertionError(msg)


def test_feed_item_identity_prefers_guid_then_hash_then_url():
    envelope = _envelope(guid="g-1", info_hash="h-1")
    assert feed_item_identity(envelope, "url-a") == FeedItemIdentity("guid", "g-1")

    envelope = _envelope(guid=None, info_hash="h-1")
    assert feed_item_identity(envelope, "url-a") == FeedItemIdentity("info_hash", "h-1")

    envelope = _envelope(
        guid=None, info_hash=None, download_url="https://example.test/get"
    )
    assert feed_item_identity(envelope, "https://example.test/get") == FeedItemIdentity(
        "url", "https://example.test/get"
    )


def test_lookup_existing_identities_uses_bounded_execute_calls():
    session = _CountingSession()
    session.lookup_results = {
        "guid": ["guid-0"],
        "info_hash": ["hash-0"],
        "url": ["url-0"],
    }
    repo = FeedRepository(session)  # type: ignore[arg-type]
    source_id = uuid4()
    identities = []
    for index in range(500):
        kind = ("guid", "info_hash", "url")[index % 3]
        identities.append(FeedItemIdentity(kind, f"{kind}-{index}"))

    existing = _run(repo.lookup_existing_identities(source_id, identities))

    assert existing == {
        FeedItemIdentity("guid", "guid-0"),
        FeedItemIdentity("info_hash", "hash-0"),
        FeedItemIdentity("url", "url-0"),
    }
    assert session.execute_calls == 3


def test_lookup_existing_identities_chunks_large_identity_sets():
    session = _CountingSession()
    repo = FeedRepository(session)  # type: ignore[arg-type]
    source_id = uuid4()
    identities = [
        FeedItemIdentity("guid", f"guid-{index}")
        for index in range(_IDENTITY_LOOKUP_CHUNK_SIZE + 1)
    ]

    _run(repo.lookup_existing_identities(source_id, identities))

    assert session.execute_calls == 2


def test_bulk_insert_observations_uses_bounded_execute_calls_for_large_page():
    session = _CountingSession()
    session.insert_return_rows = [
        ("guid-0", None, None),
        ("guid-1", None, None),
    ]
    repo = FeedRepository(session)  # type: ignore[arg-type]
    source_id = uuid4()
    observations = [
        _observation(_envelope(guid=f"guid-{index}")) for index in range(500)
    ]

    inserted = _run(repo.bulk_insert_observations(source_id, observations))

    assert inserted == {
        FeedItemIdentity("guid", "guid-0"),
        FeedItemIdentity("guid", "guid-1"),
    }
    assert session.execute_calls == 1


def test_bulk_insert_observations_chunks_when_over_chunk_size():
    session = _CountingSession()
    session.insert_return_rows = [("guid-0", None, None)]
    repo = FeedRepository(session)  # type: ignore[arg-type]
    source_id = uuid4()
    observations = [
        _observation(_envelope(guid=f"guid-{index}"))
        for index in range(_IDENTITY_LOOKUP_CHUNK_SIZE + 1)
    ]

    _run(repo.bulk_insert_observations(source_id, observations))

    assert session.execute_calls == 2


def test_bulk_insert_observations_returns_empty_for_no_rows():
    session = _CountingSession()
    repo = FeedRepository(session)  # type: ignore[arg-type]

    inserted = _run(repo.bulk_insert_observations(uuid4(), []))

    assert inserted == set()
    assert session.execute_calls == 0


def test_lookup_existing_identities_deduplicates_input_values():
    session = _CountingSession()
    session.lookup_results = {"guid": ["dup"], "info_hash": [], "url": []}
    repo = FeedRepository(session)  # type: ignore[arg-type]
    identities = [FeedItemIdentity("guid", "dup"), FeedItemIdentity("guid", "dup")]

    existing = _run(repo.lookup_existing_identities(uuid4(), identities))

    assert existing == {FeedItemIdentity("guid", "dup")}
    assert session.execute_calls == 1


@pytest.mark.parametrize(
    ("guid", "info_hash", "url", "expected"),
    [
        ("g-1", "h-1", "u-1", FeedItemIdentity("guid", "g-1")),
        (None, "h-1", "u-1", FeedItemIdentity("info_hash", "h-1")),
        (None, None, "u-1", FeedItemIdentity("url", "u-1")),
    ],
)
def test_bulk_insert_returned_identity_precedence(
    guid: str | None,
    info_hash: str | None,
    url: str,
    expected: FeedItemIdentity,
):
    session = _CountingSession()
    session.insert_return_rows = [(guid, info_hash, url)]
    repo = FeedRepository(session)  # type: ignore[arg-type]
    envelope = _envelope(guid=guid, info_hash=info_hash)
    observations = [_observation(envelope, redacted_url=url)]

    inserted = _run(repo.bulk_insert_observations(uuid4(), observations))

    assert inserted == {expected}

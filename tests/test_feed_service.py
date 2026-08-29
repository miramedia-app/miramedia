"""DB-free characterization tests for FeedObserveService (plan 438)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from miramedia.feeds.poller import FeedPollResult
from miramedia.feeds.repository import (
    FeedItemIdentity,
    FeedObservationInsert,
    FeedRepository,
    FeedSourceClaim,
    feed_item_identity,
)
from miramedia.feeds.schemas import FeedDecision, FeedEnvelope
from miramedia.feeds.service import FeedObserveService
from miramedia.indexers.models import IndexerSite
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.schemas import Movie, MovieId
from tests.fakes import build_movie_service, build_show_service


def _run(coro):
    return asyncio.run(coro)


def _feed_config(
    *,
    release_feeds_enabled: bool = True,
    release_feeds_maxage_days: int = 7,
    continuous_download: bool = True,
    jackett_enabled: bool = False,
    prowlarr_enabled: bool = False,
    native_enabled: bool = False,
    jackett_indexers: list[str] | None = None,
):
    indexers = SimpleNamespace(
        jackett=SimpleNamespace(
            enabled=jackett_enabled,
            indexers=jackett_indexers or [],
        ),
        prowlarr=SimpleNamespace(enabled=prowlarr_enabled),
        native=SimpleNamespace(enabled=native_enabled),
        timeout_seconds=30,
    )
    misc = SimpleNamespace(
        release_feeds_enabled=release_feeds_enabled,
        release_feeds_maxage_days=release_feeds_maxage_days,
        continuous_download=continuous_download,
    )
    return SimpleNamespace(indexers=indexers, misc=misc)


def _source(**overrides) -> FeedSourceClaim:
    values = {
        "id": uuid4(),
        "backend": "jackett",
        "indexer_key": "idx-a",
        "protocol": "torznab",
        "watermark_pub_date": datetime(2024, 6, 1, tzinfo=UTC),
        "watermark_guid": "wm-guid",
        "lease_owner": "lease-owner-test",
    }
    values.update(overrides)
    return FeedSourceClaim(**values)


def _envelope(
    *,
    guid: str | None = "guid-1",
    pub_date: datetime | None = None,
    title: str = "Mystery Title 2020 1080p BluRay",
    imdb_id: str | None = None,
    info_hash: str | None = None,
    download_url: str = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
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
        pub_date=pub_date or datetime.now(UTC),
        imdb_id=imdb_id,
        info_hash=info_hash,
    )


class RecordingFeedRepository:
    """Records repository calls without touching the database."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.claimed_source: FeedSourceClaim | None = None
        self.existing_identities: set[FeedItemIdentity] = set()
        self.bulk_inserted_identities: set[FeedItemIdentity] | None = None

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def call_names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    async def upsert_source(self, **kwargs):
        self._record("upsert_source", kwargs=kwargs)

    async def disable_sources_not_in(
        self, backend: str, indexer_keys: set[str]
    ) -> None:
        self._record("disable_sources_not_in", backend, indexer_keys)

    async def claim_source(self, lease_owner: str) -> FeedSourceClaim | None:
        self._record("claim_source", lease_owner)
        if self.claimed_source is None:
            return None
        return FeedSourceClaim(
            id=self.claimed_source.id,
            backend=self.claimed_source.backend,
            indexer_key=self.claimed_source.indexer_key,
            protocol=self.claimed_source.protocol,
            watermark_pub_date=self.claimed_source.watermark_pub_date,
            watermark_guid=self.claimed_source.watermark_guid,
            lease_owner=lease_owner,
        )

    async def renew_lease(self, source_id: UUID, *, lease_owner: str) -> bool:
        self._record("renew_lease", source_id, lease_owner=lease_owner)
        return True

    async def release_lease(self, source_id: UUID, *, lease_owner: str) -> bool:
        self._record("release_lease", source_id, lease_owner=lease_owner)
        return True

    async def record_poll_hold(
        self, source_id: UUID, *, lease_owner: str, reason: str, **kwargs
    ) -> bool:
        self._record(
            "record_poll_hold",
            source_id,
            lease_owner=lease_owner,
            reason=reason,
            **kwargs,
        )
        return True

    async def record_poll_success(
        self,
        source_id: UUID,
        *,
        lease_owner: str,
        watermark_pub_date: datetime | None,
        watermark_guid: str | None,
    ) -> bool:
        self._record(
            "record_poll_success",
            source_id,
            lease_owner=lease_owner,
            watermark_pub_date=watermark_pub_date,
            watermark_guid=watermark_guid,
        )
        return True

    async def lookup_existing_identities(
        self,
        source_id: UUID,
        identities: list[FeedItemIdentity],
    ) -> set[FeedItemIdentity]:
        self._record(
            "lookup_existing_identities",
            source_id,
            identities=identities,
        )
        return {
            identity for identity in identities if identity in self.existing_identities
        }

    async def bulk_insert_observations(
        self,
        source_id: UUID,
        observations: list[FeedObservationInsert],
    ) -> set[FeedItemIdentity]:
        self._record(
            "bulk_insert_observations",
            source_id,
            observations=observations,
        )
        if self.bulk_inserted_identities is None:
            return {
                feed_item_identity(obs.envelope, obs.download_url_redacted)
                for obs in observations
            }
        return {
            feed_item_identity(obs.envelope, obs.download_url_redacted)
            for obs in observations
            if feed_item_identity(obs.envelope, obs.download_url_redacted)
            in self.bulk_inserted_identities
        }

    async def purge_stale_items(self, source_id: UUID | None = None) -> int:
        self._record("purge_stale_items", source_id)
        return 0


def _service(repo: RecordingFeedRepository | None = None) -> FeedObserveService:
    db = MagicMock()
    db.commit = AsyncMock()
    db.close = AsyncMock()
    db.get = AsyncMock(return_value=None)
    svc = FeedObserveService(db=db)
    svc.repository = repo or RecordingFeedRepository()
    return svc


def _bg_services(movie_svc, show_svc):
    @asynccontextmanager
    async def bg_movie():
        yield movie_svc

    @asynccontextmanager
    async def bg_show():
        yield show_svc

    return (
        patch("miramedia.background_services.bg_movie_service", bg_movie),
        patch("miramedia.background_services.bg_show_service", bg_show),
    )


def _forbid_downloads(movie_svc, show_svc) -> None:
    for svc in (movie_svc, show_svc):
        svc.download_torrent = AsyncMock(
            side_effect=AssertionError("observe-only must not download")
        )
        svc.torrent_service.download_and_link = AsyncMock(
            side_effect=AssertionError("observe-only must not download_and_link")
        )
        svc.torrent_service.download = AsyncMock(
            side_effect=AssertionError("observe-only must not download torrents")
        )


def test_poll_once_returns_immediately_when_feeds_disabled():
    repo = RecordingFeedRepository()
    svc = _service(repo)

    with patch(
        "miramedia.feeds.service.MiraMediaConfig",
        return_value=_feed_config(release_feeds_enabled=False),
    ):
        _run(svc.poll_once())

    assert repo.calls == []


def test_poll_once_syncs_and_returns_when_no_source_claimed():
    repo = RecordingFeedRepository()
    repo.claimed_source = None
    svc = _service(repo)

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(jackett_enabled=True),
        ),
        patch("miramedia.feeds.service.jackett_feed_indexer_keys", return_value=["a"]),
        patch.object(svc, "_poll_source", AsyncMock()) as poll_source,
    ):
        _run(svc.poll_once())

    assert repo.call_names() == [
        "upsert_source",
        "disable_sources_not_in",
        "claim_source",
    ]
    poll_source.assert_not_awaited()


def test_sync_sources_jackett_upserts_and_disables_stale():
    repo = RecordingFeedRepository()
    svc = _service(repo)

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(jackett_enabled=True),
        ),
        patch(
            "miramedia.feeds.service.jackett_feed_indexer_keys",
            return_value=["alpha", "beta"],
        ),
    ):
        _run(svc.sync_sources())

    assert repo.call_names() == [
        "upsert_source",
        "upsert_source",
        "disable_sources_not_in",
    ]
    assert repo.calls[0][2]["kwargs"] == {
        "backend": "jackett",
        "indexer_key": "alpha",
        "protocol": "torznab",
        "enabled": True,
    }
    assert repo.calls[2][1] == ("jackett", {"alpha", "beta"})


def test_sync_sources_prowlarr_upserts_and_disables_stale():
    repo = RecordingFeedRepository()
    svc = _service(repo)

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(prowlarr_enabled=True),
        ),
        patch(
            "miramedia.feeds.service.prowlarr_feed_indexer_ids",
            return_value=[(7, "Indexer Seven"), (9, "Indexer Nine")],
        ),
    ):
        _run(svc.sync_sources())

    assert repo.call_names() == [
        "upsert_source",
        "upsert_source",
        "disable_sources_not_in",
    ]
    assert repo.calls[0][2]["kwargs"]["backend"] == "prowlarr"
    assert repo.calls[0][2]["kwargs"]["indexer_key"] == "7"
    assert repo.calls[0][2]["kwargs"]["protocol"] == "newznab"
    assert repo.calls[2][1] == ("prowlarr", {"7", "9"})


def test_sync_sources_native_torznab_upserts_and_disables_stale():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    site_id = uuid4()
    site = IndexerSite(
        id=site_id,
        name="Native",
        site_type="torznab",
        url="https://example.test/api",
        api_key="secret",
    )

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(native_enabled=True),
        ),
        patch(
            "miramedia.feeds.service.list_native_torznab_sites",
            AsyncMock(return_value=[site]),
        ),
    ):
        _run(svc.sync_sources())

    assert repo.call_names() == ["upsert_source", "disable_sources_not_in"]
    assert repo.calls[0][2]["kwargs"] == {
        "backend": "torznab",
        "indexer_key": str(site_id),
        "protocol": "torznab",
        "enabled": True,
    }
    assert repo.calls[1][1] == ("torznab", {str(site_id)})


def test_poll_once_claims_source_and_polls():
    repo = RecordingFeedRepository()
    source = _source()
    repo.claimed_source = source
    svc = _service(repo)

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch.object(FeedRepository, "lease_owner_id", return_value="lease-owner-1"),
        patch.object(svc, "sync_sources", AsyncMock()) as sync_sources,
        patch.object(svc, "_poll_source", AsyncMock()) as poll_source,
    ):
        _run(svc.poll_once())

    sync_sources.assert_awaited_once()
    assert repo.calls[0] == ("claim_source", ("lease-owner-1",), {})
    poll_source.assert_awaited_once()
    polled = poll_source.await_args.args[0]
    assert isinstance(polled, FeedSourceClaim)
    assert polled.id == source.id
    assert polled.lease_owner == "lease-owner-1"


def test_poll_once_releases_lease_when_poll_raises():
    repo = RecordingFeedRepository()
    source = _source()
    repo.claimed_source = source
    svc = _service(repo)

    async def _boom(_claim: FeedSourceClaim) -> None:
        msg = "poll failed"
        raise RuntimeError(msg)

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch.object(FeedRepository, "lease_owner_id", return_value="lease-owner-1"),
        patch.object(svc, "sync_sources", AsyncMock()),
        patch.object(svc, "_poll_source", _boom),
    ):
        with pytest.raises(RuntimeError, match="poll failed"):
            _run(svc.poll_once())

    assert repo.call_names() == ["claim_source", "release_lease"]
    assert repo.calls[1][2]["lease_owner"] == "lease-owner-1"
    assert svc.db.commit.await_count == 2


def test_poll_once_commits_lease_before_poll():
    repo = RecordingFeedRepository()
    source = _source()
    repo.claimed_source = source
    svc = _service(repo)
    order: list[str] = []

    async def _track_commit() -> None:
        order.append("commit")

    async def _poll(_claim: FeedSourceClaim) -> None:
        order.append("poll")

    svc.db.commit = AsyncMock(side_effect=_track_commit)

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch.object(svc, "sync_sources", AsyncMock()),
        patch.object(svc, "_poll_source", _poll),
    ):
        _run(svc.poll_once())

    assert order == ["commit", "poll"]
    assert svc.db.commit.await_count == 1


def test_poll_source_releases_session_before_fetch():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    source = _source()
    order: list[str] = []

    async def _release(_db: object) -> None:
        order.append("release")

    def _fetch(_claim: FeedSourceClaim, _site=None):
        order.append("fetch")
        return FeedPollResult(envelopes=[], http_error="HTTP 503")

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch(
            "miramedia.feeds.service.release_session_before_external_io",
            side_effect=_release,
        ),
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    assert order == ["release", "fetch"]
    assert repo.call_names() == ["record_poll_hold"]


def test_poll_source_torznab_missing_site_records_hold():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    site_id = uuid4()
    source = _source(backend="torznab", indexer_key=str(site_id))
    svc.db.get = AsyncMock(return_value=None)

    with patch(
        "miramedia.feeds.service.MiraMediaConfig",
        return_value=_feed_config(),
    ):
        _run(svc._poll_source(source))

    svc.db.get.assert_awaited_once_with(IndexerSite, site_id)
    assert repo.calls == [
        (
            "record_poll_hold",
            (source.id,),
            {"lease_owner": source.lease_owner, "reason": "site missing"},
        ),
    ]


def test_poll_source_http_error_records_hold_without_insert():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    source = _source()

    def _fetch(_claim: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[], http_error="HTTP 503")

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch(
            "miramedia.feeds.service.release_session_before_external_io",
            AsyncMock(),
        ),
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    assert repo.call_names() == ["record_poll_hold"]
    assert repo.calls[-1] == (
        "record_poll_hold",
        (source.id,),
        {"lease_owner": source.lease_owner, "reason": "HTTP 503"},
    )
    assert "bulk_insert_observations" not in repo.call_names()


def test_poll_source_parse_error_records_hold_without_insert():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    source = _source()

    def _fetch(_claim: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[], parse_error="xml parse failed")

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch(
            "miramedia.feeds.service.release_session_before_external_io",
            AsyncMock(),
        ),
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    assert repo.call_names() == ["record_poll_hold"]
    assert repo.calls[-1] == (
        "record_poll_hold",
        (source.id,),
        {"lease_owner": source.lease_owner, "reason": "xml parse failed"},
    )
    assert "bulk_insert_observations" not in repo.call_names()


def test_poll_source_empty_filtered_envelopes_keeps_watermarks():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    watermark = datetime(2024, 6, 1, tzinfo=UTC)
    source = _source(
        watermark_pub_date=watermark,
        watermark_guid="keep-guid",
    )
    stale = FeedEnvelope(
        result=IndexerQueryResult(
            title="Old Release 2010 720p",
            download_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            seeders=1,
            flags=[],
            size=1,
            usenet=False,
            age=30,
            indexer="test-indexer",
        ),
        provider_guid=None,
        pub_date=watermark - timedelta(days=30),
    )

    def _fetch(_feed_source_obj: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[stale])

    with patch(
        "miramedia.feeds.service.MiraMediaConfig",
        return_value=_feed_config(release_feeds_maxage_days=7),
    ):
        with patch.object(svc, "_fetch_source", _fetch):
            _run(svc._poll_source(source))

    assert repo.call_names() == ["renew_lease", "record_poll_success"]
    assert repo.calls[-1] == (
        "record_poll_success",
        (source.id,),
        {
            "lease_owner": source.lease_owner,
            "watermark_pub_date": watermark,
            "watermark_guid": "keep-guid",
        },
    )


def test_poll_source_skips_duplicate_items_without_insert():
    repo = RecordingFeedRepository()
    repo.existing_identities = {FeedItemIdentity("guid", "guid-1")}
    svc = _service(repo)
    source = _source()
    envelope = _envelope()
    movie_svc, _, _ = build_movie_service()
    show_svc, _, _ = build_show_service()
    _forbid_downloads(movie_svc, show_svc)
    movie_svc.get_all_movies = AsyncMock(return_value=[])
    show_svc.get_all_shows = AsyncMock(return_value=[])

    def _fetch(_feed_source_obj: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[envelope])

    bg_movie_patch, bg_show_patch = _bg_services(movie_svc, show_svc)
    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        bg_movie_patch,
        bg_show_patch,
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    assert repo.call_names() == [
        "renew_lease",
        "lookup_existing_identities",
        "bulk_insert_observations",
        "record_poll_success",
        "purge_stale_items",
    ]
    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert bulk_calls[0][2]["observations"] == []


def test_poll_source_records_unmatched_observation():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    source = _source()
    envelope = _envelope(title="Unknown Series S01E01 1080p")
    movie_svc, _, _ = build_movie_service()
    show_svc, _, _ = build_show_service()
    _forbid_downloads(movie_svc, show_svc)
    movie_svc.get_all_movies = AsyncMock(return_value=[])
    show_svc.get_all_shows = AsyncMock(return_value=[])

    def _fetch(_feed_source_obj: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[envelope])

    bg_movie_patch, bg_show_patch = _bg_services(movie_svc, show_svc)
    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        bg_movie_patch,
        bg_show_patch,
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert len(bulk_calls) == 1
    assert len(bulk_calls[0][2]["observations"]) == 1
    assert bulk_calls[0][2]["observations"][0].decision == FeedDecision.unmatched
    assert bulk_calls[0][2]["observations"][0].bound_media_type is None


def test_poll_source_records_matched_would_grab_without_downloads():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    source = _source()
    movie_id = MovieId(uuid4())
    movie = Movie(
        id=movie_id,
        name="Inception",
        year=2010,
        library="default",
        overview="",
        metadata_provider="tmdb",
        external_id="1",
        imdb_id="tt1375666",
        skipped=False,
        continuous_download=True,
    )
    envelope = _envelope(imdb_id="tt1375666", title="Inception 2010 1080p BluRay")
    movie_svc, _, _ = build_movie_service()
    show_svc, _, _ = build_show_service()
    _forbid_downloads(movie_svc, show_svc)
    movie_svc.get_all_movies = AsyncMock(return_value=[movie])
    show_svc.get_all_shows = AsyncMock(return_value=[])

    def _fetch(_feed_source_obj: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[envelope])

    evaluate_gates = AsyncMock(return_value=(FeedDecision.would_grab, 88))
    bg_movie_patch, bg_show_patch = _bg_services(movie_svc, show_svc)
    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        bg_movie_patch,
        bg_show_patch,
        patch.object(svc, "_fetch_source", _fetch),
        patch("miramedia.feeds.service.evaluate_observe_gates", evaluate_gates),
    ):
        _run(svc._poll_source(source))

    evaluate_gates.assert_awaited_once()
    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert len(bulk_calls) == 1
    assert len(bulk_calls[0][2]["observations"]) == 1
    assert bulk_calls[0][2]["observations"][0].decision == FeedDecision.would_grab
    assert bulk_calls[0][2]["observations"][0].bound_media_type == "movie"
    assert bulk_calls[0][2]["observations"][0].bound_media_id == movie_id
    assert bulk_calls[0][2]["observations"][0].score == 88


def test_poll_source_success_advances_watermark_clears_lease_and_purges_stale():
    repo = RecordingFeedRepository()
    svc = _service(repo)
    now = datetime.now(UTC)
    old_watermark = now - timedelta(days=30)
    new_pub_date = now - timedelta(days=1)
    source = _source(
        watermark_pub_date=old_watermark,
        watermark_guid="old-guid",
    )
    envelope = _envelope(guid="new-guid", pub_date=new_pub_date)
    movie_svc, _, _ = build_movie_service()
    show_svc, _, _ = build_show_service()
    _forbid_downloads(movie_svc, show_svc)
    movie_svc.get_all_movies = AsyncMock(return_value=[])
    show_svc.get_all_shows = AsyncMock(return_value=[])

    def _fetch(_feed_source_obj: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[envelope])

    bg_movie_patch, bg_show_patch = _bg_services(movie_svc, show_svc)
    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        bg_movie_patch,
        bg_show_patch,
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    assert repo.call_names()[0] == "renew_lease"
    success_calls = [call for call in repo.calls if call[0] == "record_poll_success"]
    assert len(success_calls) == 1
    assert success_calls[0][2]["watermark_pub_date"] == new_pub_date
    assert success_calls[0][2]["watermark_guid"] == "new-guid"
    assert repo.call_names()[-1] == "purge_stale_items"
    assert repo.calls[-1][1] == (source.id,)


def test_poll_source_abandons_when_renew_fails():
    repo = RecordingFeedRepository()
    renew = AsyncMock(return_value=False)
    repo.renew_lease = renew  # type: ignore[method-assign]
    svc = _service(repo)
    source = _source()
    envelope = _envelope()

    def _fetch(_claim: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=[envelope])

    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        patch(
            "miramedia.feeds.service.release_session_before_external_io",
            AsyncMock(),
        ),
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))

    renew.assert_awaited_once_with(source.id, lease_owner=source.lease_owner)
    assert "record_poll_success" not in repo.call_names()
    assert "bulk_insert_observations" not in repo.call_names()


def _poll_with_envelopes(
    repo: RecordingFeedRepository,
    source: FeedSourceClaim,
    envelopes: list[FeedEnvelope],
) -> None:
    svc = _service(repo)
    movie_svc, _, _ = build_movie_service()
    show_svc, _, _ = build_show_service()
    _forbid_downloads(movie_svc, show_svc)
    movie_svc.get_all_movies = AsyncMock(return_value=[])
    show_svc.get_all_shows = AsyncMock(return_value=[])

    def _fetch(_feed_source_obj: FeedSourceClaim, _site=None):
        return FeedPollResult(envelopes=envelopes)

    bg_movie_patch, bg_show_patch = _bg_services(movie_svc, show_svc)
    with (
        patch(
            "miramedia.feeds.service.MiraMediaConfig",
            return_value=_feed_config(),
        ),
        bg_movie_patch,
        bg_show_patch,
        patch.object(svc, "_fetch_source", _fetch),
    ):
        _run(svc._poll_source(source))


def test_poll_source_skips_duplicate_guid_from_db():
    repo = RecordingFeedRepository()
    repo.existing_identities = {FeedItemIdentity("guid", "dup-guid")}
    source = _source()
    envelope = _envelope(guid="dup-guid")

    _poll_with_envelopes(repo, source, [envelope])

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert bulk_calls[0][2]["observations"] == []


def test_poll_source_skips_duplicate_info_hash_from_db():
    repo = RecordingFeedRepository()
    repo.existing_identities = {FeedItemIdentity("info_hash", "abc123")}
    source = _source()
    envelope = _envelope(guid=None, info_hash="abc123")

    _poll_with_envelopes(repo, source, [envelope])

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert bulk_calls[0][2]["observations"] == []


def test_poll_source_skips_duplicate_redacted_url_from_db():
    repo = RecordingFeedRepository()
    redacted = "magnet:?xt=urn:btih:<redacted>"
    repo.existing_identities = {FeedItemIdentity("url", redacted)}
    source = _source()
    envelope = _envelope(
        guid=None,
        info_hash=None,
        download_url="magnet:?xt=urn:btih:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )

    with patch("miramedia.feeds.service.redact_download_url", return_value=redacted):
        _poll_with_envelopes(repo, source, [envelope])

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert bulk_calls[0][2]["observations"] == []


def test_poll_source_mixed_new_and_existing_page_inserts_only_new():
    repo = RecordingFeedRepository()
    repo.existing_identities = {FeedItemIdentity("guid", "old-guid")}
    source = _source()
    existing = _envelope(guid="old-guid", title="Old Release 2019 720p")
    new_item = _envelope(guid="new-guid", title="Unknown Series S01E01 1080p")

    _poll_with_envelopes(repo, source, [existing, new_item])

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    observations = bulk_calls[0][2]["observations"]
    assert len(observations) == 1
    assert observations[0].envelope.provider_guid == "new-guid"


def test_poll_source_same_page_duplicate_guid_first_envelope_wins():
    repo = RecordingFeedRepository()
    source = _source()
    first = _envelope(guid="same-guid", title="First Release 2020 1080p")
    second = _envelope(guid="same-guid", title="Second Release 2020 1080p")

    _poll_with_envelopes(repo, source, [first, second])

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    observations = bulk_calls[0][2]["observations"]
    assert len(observations) == 1
    assert observations[0].envelope.result.title == "First Release 2020 1080p"


def test_poll_source_conflicting_identities_on_same_page_both_inserted():
    repo = RecordingFeedRepository()
    source = _source()
    hash_only = FeedEnvelope(
        result=IndexerQueryResult(
            title="Hash Only 2020 1080p",
            download_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            seeders=5,
            flags=[],
            size=1,
            usenet=False,
            age=1,
            indexer="test-indexer",
        ),
        provider_guid=None,
        pub_date=datetime.now(UTC),
        info_hash="shared-hash",
    )
    guid_and_hash = FeedEnvelope(
        result=IndexerQueryResult(
            title="Guid And Hash 2020 1080p",
            download_url="magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98",
            seeders=5,
            flags=[],
            size=1,
            usenet=False,
            age=1,
            indexer="test-indexer",
        ),
        provider_guid="guid-with-hash",
        pub_date=datetime.now(UTC),
        info_hash="shared-hash",
    )

    _poll_with_envelopes(repo, source, [hash_only, guid_and_hash])

    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    observations = bulk_calls[0][2]["observations"]
    assert len(observations) == 2
    assert observations[0].envelope.info_hash == "shared-hash"
    assert observations[0].envelope.provider_guid is None
    assert observations[1].envelope.provider_guid == "guid-with-hash"


def test_poll_source_uses_bounded_repository_calls_for_large_page():
    repo = RecordingFeedRepository()
    source = _source()
    envelopes = [_envelope(guid=f"guid-{index}") for index in range(500)]

    _poll_with_envelopes(repo, source, envelopes)

    lookup_calls = [
        call for call in repo.calls if call[0] == "lookup_existing_identities"
    ]
    bulk_calls = [call for call in repo.calls if call[0] == "bulk_insert_observations"]
    assert len(lookup_calls) == 1
    assert len(bulk_calls) == 1
    assert len(bulk_calls[0][2]["observations"]) == 500

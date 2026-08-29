"""Observe-only release feed orchestration (design 385 Slice A)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.database import release_session_before_external_io
from miramedia.feeds.bind import (
    FeedBindResult,
    bind_feed_envelope_indexed,
    build_feed_catalog,
)
from miramedia.feeds.gates import evaluate_observe_gates, evaluate_unmatched_observe
from miramedia.feeds.metrics import inc as feed_metric_inc
from miramedia.feeds.poller import (
    FeedPoller,
    FeedPollResult,
    jackett_feed_indexer_keys,
    list_native_torznab_sites,
    prowlarr_feed_indexer_ids,
)
from miramedia.feeds.redact import redact_download_url
from miramedia.feeds.repository import (
    FeedItemIdentity,
    FeedObservationInsert,
    FeedRepository,
    FeedSourceClaim,
    feed_item_identity,
)
from miramedia.feeds.schemas import FeedDecision, FeedEnvelope
from miramedia.indexers.models import IndexerSite

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _maxage_cutoffs(
    now: datetime,
    watermark_pub_date: datetime | None,
    maxage_days: int,
) -> tuple[datetime, datetime]:
    """Return absolute retention floor and watermark-relative ordering bound."""
    absolute_retention_cutoff = now - timedelta(days=maxage_days)
    if watermark_pub_date is None:
        watermark_ordering_cutoff = absolute_retention_cutoff
    else:
        watermark_ordering_cutoff = watermark_pub_date - timedelta(days=maxage_days)
    return absolute_retention_cutoff, watermark_ordering_cutoff


class FeedObserveService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repository = FeedRepository(db)
        self.poller = FeedPoller()

    async def sync_sources(self) -> None:
        cfg = MiraMediaConfig().indexers
        if cfg.jackett.enabled:
            keys = jackett_feed_indexer_keys()
            for key in keys:
                await self.repository.upsert_source(
                    backend="jackett",
                    indexer_key=key,
                    protocol="torznab",
                    enabled=True,
                )
            await self.repository.disable_sources_not_in("jackett", set(keys))

        if cfg.prowlarr.enabled:
            indexers = prowlarr_feed_indexer_ids()
            keys = {str(iid) for iid, _ in indexers}
            for iid, _name in indexers:
                await self.repository.upsert_source(
                    backend="prowlarr",
                    indexer_key=str(iid),
                    protocol="newznab",
                    enabled=True,
                )
            await self.repository.disable_sources_not_in("prowlarr", keys)

        if cfg.native.enabled:
            sites = await list_native_torznab_sites(self.db)
            keys = {str(site.id) for site in sites}
            for site in sites:
                await self.repository.upsert_source(
                    backend="torznab",
                    indexer_key=str(site.id),
                    protocol="torznab",
                    enabled=True,
                )
            await self.repository.disable_sources_not_in("torznab", keys)

    async def poll_once(self) -> None:
        if not MiraMediaConfig().misc.release_feeds_enabled:
            return

        await self.sync_sources()
        lease_owner = FeedRepository.lease_owner_id()
        claim = await self.repository.claim_source(lease_owner)
        if claim is None:
            log.debug("No feed source available to poll")
            return

        await self.db.commit()
        try:
            await self._poll_source(claim)
        except Exception:
            await self.repository.release_lease(claim.id, lease_owner=claim.lease_owner)
            await self.db.commit()
            raise

    async def _poll_source(self, claim: FeedSourceClaim) -> None:
        maxage_days = MiraMediaConfig().misc.release_feeds_maxage_days
        torznab_site = None
        if claim.backend == "torznab":
            torznab_site = await self.db.get(IndexerSite, UUID(claim.indexer_key))
            if torznab_site is None:
                await self.repository.record_poll_hold(
                    claim.id,
                    lease_owner=claim.lease_owner,
                    reason="site missing",
                )
                feed_metric_inc("feed_poll_errors")
                return

        await release_session_before_external_io(self.db)
        poll_result = await asyncio.to_thread(self._fetch_source, claim, torznab_site)
        if poll_result.http_error or poll_result.parse_error:
            reason = poll_result.http_error or poll_result.parse_error or "error"
            log.warning(
                "feed_poll_hold",
                extra={
                    "source": f"{claim.backend}:{claim.indexer_key}",
                    "reason": reason,
                },
            )
            await self.repository.record_poll_hold(
                claim.id,
                lease_owner=claim.lease_owner,
                reason=reason,
            )
            feed_metric_inc("feed_poll_errors")
            return

        if not await self.repository.renew_lease(
            claim.id, lease_owner=claim.lease_owner
        ):
            log.warning(
                "feed_poll_abandoned",
                extra={
                    "source": f"{claim.backend}:{claim.indexer_key}",
                    "reason": "lease lost after fetch",
                },
            )
            return

        envelopes = self._filter_by_maxage(
            poll_result.envelopes,
            claim.watermark_pub_date,
            maxage_days,
        )
        if not envelopes:
            await self.repository.record_poll_success(
                claim.id,
                lease_owner=claim.lease_owner,
                watermark_pub_date=claim.watermark_pub_date,
                watermark_guid=claim.watermark_guid,
            )
            log.info(
                "feed_poll_ok",
                extra={
                    "source": f"{claim.backend}:{claim.indexer_key}",
                    "item_count": 0,
                    "advanced_watermark": False,
                },
            )
            return

        processed_dates: list[datetime] = []
        last_guid: str | None = claim.watermark_guid

        from miramedia.background_services import bg_movie_service, bg_show_service

        async with bg_movie_service() as movie_svc, bg_show_service() as show_svc:
            movies = await movie_svc.get_all_movies()
            shows = await show_svc.get_all_shows()
            global_cd = MiraMediaConfig().misc.continuous_download
            catalog = build_feed_catalog(
                movies=movies,
                shows=shows,
                global_continuous_download=global_cd,
            )

            envelope_data = [
                (
                    envelope,
                    redact_download_url(envelope.result.download_url),
                )
                for envelope in envelopes
            ]
            identities = [
                feed_item_identity(envelope, redacted_url)
                for envelope, redacted_url in envelope_data
            ]
            existing_identities = await self.repository.lookup_existing_identities(
                claim.id,
                identities,
            )
            seen_page: set[FeedItemIdentity] = set()
            pending: list[
                tuple[FeedEnvelope, str, FeedObservationInsert, FeedBindResult]
            ] = []

            for (envelope, redacted_url), identity in zip(
                envelope_data, identities, strict=True
            ):
                feed_metric_inc("feed_items_seen")
                if identity in existing_identities or identity in seen_page:
                    if envelope.pub_date:
                        processed_dates.append(envelope.pub_date)
                    if envelope.provider_guid:
                        last_guid = envelope.provider_guid
                    continue

                seen_page.add(identity)

                bind = bind_feed_envelope_indexed(envelope, catalog)
                if bind.media_type is None:
                    decision = await evaluate_unmatched_observe(envelope)
                    score = None
                    feed_metric_inc("feed_items_unmatched")
                else:
                    decision, score = await evaluate_observe_gates(
                        envelope,
                        media_type=bind.media_type,
                        movie=bind.movie,
                        show=bind.show,
                        movie_service=movie_svc if bind.media_type == "movie" else None,
                        show_service=show_svc if bind.media_type == "show" else None,
                        torrent_service=movie_svc.torrent_service,
                    )
                    if decision == FeedDecision.would_grab:
                        feed_metric_inc("feed_would_grab")

                pending.append(
                    (
                        envelope,
                        redacted_url,
                        FeedObservationInsert(
                            envelope=envelope,
                            download_url_redacted=redacted_url,
                            bound_media_type=bind.media_type,
                            bound_media_id=bind.media_id,
                            decision=decision,
                            score=score,
                        ),
                        bind,
                    )
                )

                if envelope.pub_date:
                    processed_dates.append(envelope.pub_date)
                if envelope.provider_guid:
                    last_guid = envelope.provider_guid

            inserted_identities = await self.repository.bulk_insert_observations(
                claim.id,
                [row for _, _, row, _ in pending],
            )
            for envelope, _redacted_url, observation, bind in pending:
                identity = feed_item_identity(
                    envelope, observation.download_url_redacted
                )
                if identity not in inserted_identities:
                    continue
                log.info(
                    "feed_decision",
                    extra={
                        "guid": envelope.provider_guid,
                        "indexer": envelope.result.indexer,
                        "bound_media_id": str(bind.media_id) if bind.media_id else None,
                        "decision": observation.decision.value,
                        "score": observation.score,
                    },
                )

        new_watermark = self._advance_watermark(
            claim.watermark_pub_date,
            processed_dates,
            len(poll_result.envelopes),
        )
        await self.repository.record_poll_success(
            claim.id,
            lease_owner=claim.lease_owner,
            watermark_pub_date=new_watermark,
            watermark_guid=last_guid,
        )
        await self.repository.purge_stale_items(claim.id)
        log.info(
            "feed_poll_ok",
            extra={
                "source": f"{claim.backend}:{claim.indexer_key}",
                "item_count": len(envelopes),
                "advanced_watermark": new_watermark != claim.watermark_pub_date,
            },
        )

    def _fetch_source(
        self,
        claim: FeedSourceClaim,
        torznab_site: IndexerSite | None = None,
    ) -> FeedPollResult:
        if claim.backend == "jackett":
            return self.poller.poll_jackett(claim.indexer_key)
        if claim.backend == "prowlarr":
            return self.poller.poll_prowlarr(int(claim.indexer_key))
        if claim.backend == "torznab":
            if torznab_site is None:
                return FeedPollResult(envelopes=[], http_error="site missing")
            return self.poller.poll_torznab_site(torznab_site)
        return FeedPollResult(envelopes=[], http_error="unknown backend")

    @staticmethod
    def _filter_by_maxage(
        envelopes: list[FeedEnvelope],
        watermark_pub_date: datetime | None,
        maxage_days: int,
    ) -> list[FeedEnvelope]:
        if maxage_days <= 0:
            return envelopes
        absolute_retention_cutoff, watermark_ordering_cutoff = _maxage_cutoffs(
            _utc_now(),
            watermark_pub_date,
            maxage_days,
        )
        ordering_cutoff = min(absolute_retention_cutoff, watermark_ordering_cutoff)
        kept: list[FeedEnvelope] = []
        for envelope in envelopes:
            if envelope.pub_date is None:
                kept.append(envelope)
                continue
            if envelope.pub_date < absolute_retention_cutoff:
                continue
            if envelope.pub_date >= ordering_cutoff:
                kept.append(envelope)
            elif envelope.provider_guid:
                # Late/out-of-order GUID still accepted within absolute retention.
                kept.append(envelope)
        return kept

    @staticmethod
    def _advance_watermark(
        current: datetime | None,
        processed_dates: list[datetime],
        page_count: int,
    ) -> datetime | None:
        if not processed_dates:
            return current
        max_date = max(processed_dates)
        if current is None or max_date > current:
            return max_date
        # Page complete heuristic: fewer than limit means we likely saw everything new.
        if page_count < 500:
            return max(current, max_date)
        return current

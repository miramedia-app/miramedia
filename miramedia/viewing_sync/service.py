"""Jellyfin viewing-state dry-run orchestration (design 386 Slice A)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.database import release_session_before_external_io
from miramedia.playback.bulk import UserMediaKey
from miramedia.playback.repository import PlaybackRepository, UserFileKey
from miramedia.playback.schemas import MediaKind as PlaybackMediaKind
from miramedia.viewing_sync.config import ViewingSyncConfig
from miramedia.viewing_sync.files import bulk_pick_playable_files
from miramedia.viewing_sync.jellyfin.client import (
    JellyfinClient,
    JellyfinError,
    jellyfin_item_to_event,
)
from miramedia.viewing_sync.matcher import (
    MediaCatalog,
    match_event_media,
    quarantine_from_match,
)
from miramedia.viewing_sync.metrics import inc as viewing_metric_inc
from miramedia.viewing_sync.proposal import LocalViewingSnapshot, build_proposal
from miramedia.viewing_sync.redact import redact_secret_text
from miramedia.viewing_sync.repository import (
    ConnectorItemKey,
    ViewingSyncRepository,
)
from miramedia.viewing_sync.schemas import (
    DryRunMetrics,
    ExternalViewingEvent,
    MatchConfidence,
    MediaMatchResult,
    ProposalAction,
    QuarantineReason,
    QuarantineRecord,
    ViewingProposal,
)

log = logging.getLogger(__name__)
_CONNECTOR = "jellyfin"


@dataclass(frozen=True, slots=True)
class _MatchedEvent:
    event: ExternalViewingEvent
    miramedia_user_id: UUID
    media_kind: PlaybackMediaKind
    media_id: UUID
    match: MediaMatchResult


class ViewingSyncDryRunService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repository = ViewingSyncRepository(db)
        self.playback = PlaybackRepository(db)

    async def poll_once(self) -> DryRunMetrics | None:
        cfg = MiraMediaConfig().viewing_sync
        if not cfg.enabled:
            return None

        try:
            user_map = ViewingSyncConfig.validate_user_map(cfg.jellyfin.user_map)
        except ValueError as exc:
            log.warning("viewing_sync_config_error", extra={"reason": str(exc)})
            viewing_metric_inc("viewing_sync_poll_errors")
            return None

        if not user_map:
            log.warning("viewing_sync_disabled_no_user_map")
            return None

        run = await self.repository.start_run(_CONNECTOR)
        metrics = DryRunMetrics(users_mapped=len(user_map))
        error_redacted: str | None = None
        status = "success"

        try:
            catalog = await self.repository.load_media_catalog()
            user_cursors = await self.repository.get_user_cursors(
                _CONNECTOR, list(user_map.keys())
            )
            await release_session_before_external_io(self.db)

            jellyfin_cfg = cfg.jellyfin
            fetch_result = await asyncio.to_thread(
                self._fetch_jellyfin_events,
                jellyfin_cfg.url,
                jellyfin_cfg.api_key,
                jellyfin_cfg.timeout_seconds,
                jellyfin_cfg.allow_private_network,
                jellyfin_cfg.allow_insecure_transport,
                user_map,
                user_cursors,
            )
            metrics.users_seen = fetch_result.users_seen

            outcomes = await self._process_events_batch(
                run_id=run.id,
                events=fetch_result.events,
                user_map=user_map,
                catalog=catalog,
                metrics=metrics,
            )
            for outcome in outcomes:
                if outcome is not None:
                    log.info(
                        "viewing_sync_dry_run_item",
                        extra={
                            "action": outcome.action.value,
                            "reason": outcome.reason,
                            "mira_media_id": str(outcome.media_id)
                            if outcome.media_id
                            else None,
                            "jellyfin_item_id": outcome.connector_item_id,
                            "payload_digest": outcome.payload_digest,
                        },
                    )

            for (
                connector_user_id,
                max_remote_at,
            ) in fetch_result.user_max_remote_at.items():
                if max_remote_at is not None:
                    await self.repository.set_user_cursor(
                        _CONNECTOR, connector_user_id, max_remote_at
                    )
            await self.repository.purge_stale_rows(
                retention_days=cfg.retention_days,
                retention_min_rows=cfg.retention_min_rows,
            )
        except JellyfinError as exc:
            status = "error"
            metrics.errors += 1
            error_redacted = redact_secret_text(str(exc), api_key=cfg.jellyfin.api_key)
            viewing_metric_inc("viewing_sync_poll_errors")
            log.warning("viewing_sync_poll_error", extra={"reason": error_redacted})
        except Exception:
            status = "error"
            metrics.errors += 1
            viewing_metric_inc("viewing_sync_poll_errors")
            log.exception("viewing_sync_poll_failed")
        finally:
            await self.repository.finish_run(
                run.id,
                status=status,
                metrics=metrics.to_dict(),
                error_redacted=error_redacted,
            )
            log.info(
                "viewing_sync_dry_run_summary",
                extra={"connector": _CONNECTOR, **metrics.to_dict()},
            )

        return metrics

    async def _process_events_batch(
        self,
        *,
        run_id: UUID,
        events: list[ExternalViewingEvent],
        user_map: dict[str, UUID],
        catalog: MediaCatalog,
        metrics: DryRunMetrics,
    ) -> list[ViewingProposal | None]:
        quarantine_records: list[QuarantineRecord] = []
        matched_events: list[_MatchedEvent] = []
        matched_indices: list[int] = []
        outcomes: list[ViewingProposal | None] = []

        for event in events:
            metrics.items_seen += 1
            metrics.items_with_play_signal += 1
            viewing_metric_inc("viewing_sync_items_seen")

            miramedia_user_id = user_map.get(event.connector_user_id)
            if miramedia_user_id is None:
                record = QuarantineRecord(
                    reason=QuarantineReason.unmapped_user,
                    connector_user_id=event.connector_user_id,
                    connector_item_id=event.connector_item_id,
                    item_type=event.media_kind.value,
                    provider_ids=dict(event.provider_ids),
                    title=event.title,
                    year=event.year,
                    series_name=event.series_name,
                    season=event.season_number,
                    episode=event.episode_number,
                )
                quarantine_records.append(record)
                metrics.record_quarantine(record)
                viewing_metric_inc("viewing_sync_quarantined")
                outcomes.append(None)
                continue

            match = match_event_media(event, catalog=catalog)
            if match.confidence != MatchConfidence.unique or match.media_id is None:
                record = quarantine_from_match(event, match)
                if record is not None:
                    quarantine_records.append(record)
                    metrics.record_quarantine(record)
                    viewing_metric_inc("viewing_sync_quarantined")
                outcomes.append(None)
                continue

            metrics.unique_matches += 1
            viewing_metric_inc("viewing_sync_unique_matches")

            playback_kind = (
                PlaybackMediaKind.movie
                if match.media_kind and match.media_kind.value == "movie"
                else PlaybackMediaKind.episode
            )
            matched_events.append(
                _MatchedEvent(
                    event=event,
                    miramedia_user_id=miramedia_user_id,
                    media_kind=playback_kind,
                    media_id=match.media_id,
                    match=match,
                )
            )
            matched_indices.append(len(outcomes))
            outcomes.append(None)

        proposals: list[ViewingProposal] = []
        if matched_events:
            resolved, matched_quarantines = await self._resolve_matched_events(
                matched_events=matched_events,
                metrics=metrics,
            )
            quarantine_records.extend(matched_quarantines)
            for index, proposal in zip(matched_indices, resolved, strict=True):
                outcomes[index] = proposal
                if proposal is not None:
                    proposals.append(proposal)

        await self.repository.insert_quarantines_batch(run_id, quarantine_records)
        await self.repository.insert_proposals_batch(run_id, proposals)
        return outcomes

    async def _resolve_matched_events(
        self,
        *,
        matched_events: list[_MatchedEvent],
        metrics: DryRunMetrics,
    ) -> tuple[list[ViewingProposal | None], list[QuarantineRecord]]:
        media_keys = [
            UserMediaKey(
                user_id=item.miramedia_user_id,
                media_kind=item.media_kind,
                media_id=item.media_id,
            )
            for item in matched_events
        ]
        connector_keys = [
            ConnectorItemKey(
                connector=item.event.connector,
                connector_user_id=item.event.connector_user_id,
                connector_item_id=item.event.connector_item_id,
            )
            for item in matched_events
        ]

        playables = await bulk_pick_playable_files(self.db, media_keys)
        file_keys = [
            UserFileKey(
                user_id=item.miramedia_user_id,
                file_id=playable.file_id,
                media_kind=item.media_kind,
            )
            for item in matched_events
            if (
                playable := playables.get(
                    UserMediaKey(
                        user_id=item.miramedia_user_id,
                        media_kind=item.media_kind,
                        media_id=item.media_id,
                    )
                )
            )
            is not None
        ]

        progress_map, watch_map, digest_map = await asyncio.gather(
            self.playback.bulk_get_progress(file_keys),
            self.playback.bulk_get_watched(media_keys),
            self.repository.bulk_get_prior_digests(connector_keys),
        )

        running_digests = dict(digest_map)
        resolved: list[ViewingProposal | None] = []
        quarantine_records: list[QuarantineRecord] = []

        for item in matched_events:
            media_key = UserMediaKey(
                user_id=item.miramedia_user_id,
                media_kind=item.media_kind,
                media_id=item.media_id,
            )
            event = item.event

            playable = playables.get(media_key)
            if playable is None:
                record = QuarantineRecord(
                    reason=QuarantineReason.no_playable_file,
                    connector_user_id=event.connector_user_id,
                    connector_item_id=event.connector_item_id,
                    item_type=event.media_kind.value,
                    provider_ids=dict(event.provider_ids),
                    candidate_mira_ids=(item.media_id,),
                    title=event.title,
                    year=event.year,
                    series_name=event.series_name,
                    season=event.season_number,
                    episode=event.episode_number,
                )
                quarantine_records.append(record)
                metrics.record_quarantine(record)
                viewing_metric_inc("viewing_sync_quarantined")
                resolved.append(None)
                continue

            connector_key = ConnectorItemKey(
                connector=event.connector,
                connector_user_id=event.connector_user_id,
                connector_item_id=event.connector_item_id,
            )
            file_key = UserFileKey(
                user_id=item.miramedia_user_id,
                file_id=playable.file_id,
                media_kind=item.media_kind,
            )
            local_progress = progress_map.get(file_key)
            local_watch = watch_map.get(media_key)
            prior_digest = running_digests.get(connector_key)

            proposal = build_proposal(
                event,
                miramedia_user_id=item.miramedia_user_id,
                match=item.match,
                file_id=playable.file_id,
                local=LocalViewingSnapshot(
                    progress=local_progress,
                    watch=local_watch,
                ),
                prior_digest=prior_digest,
            )

            if proposal.action == ProposalAction.quarantine:
                record = QuarantineRecord(
                    reason=QuarantineReason.clock_skew,
                    connector_user_id=event.connector_user_id,
                    connector_item_id=event.connector_item_id,
                    item_type=event.media_kind.value,
                    provider_ids=dict(event.provider_ids),
                    candidate_mira_ids=(item.media_id,),
                    title=event.title,
                    year=event.year,
                    series_name=event.series_name,
                    season=event.season_number,
                    episode=event.episode_number,
                )
                quarantine_records.append(record)
                metrics.record_quarantine(record)
                viewing_metric_inc("viewing_sync_quarantined")
                resolved.append(None)
                continue

            metrics.record_proposal(proposal)
            running_digests[connector_key] = proposal.payload_digest
            resolved.append(proposal)

        return resolved, quarantine_records

    @staticmethod
    def _fetch_jellyfin_events(
        url: str,
        api_key: str,
        timeout_seconds: int,
        allow_private_network: bool,
        allow_insecure_transport: bool,
        user_map: dict[str, UUID],
        user_cursors: dict[str, datetime | None],
    ) -> _FetchResult:
        events: list[ExternalViewingEvent] = []
        users_seen = 0
        users_missing = 0
        user_max_remote_at: dict[str, datetime | None] = {}
        with JellyfinClient(
            url=url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            allow_private_network=allow_private_network,
            allow_insecure_transport=allow_insecure_transport,
        ) as client:
            known_users = {user.id for user in client.list_users()}
            for jellyfin_user_id in user_map:
                if jellyfin_user_id not in known_users:
                    users_missing += 1
                    viewing_metric_inc("viewing_sync_users_missing")
                    log.warning(
                        "viewing_sync_user_missing",
                        extra={"connector_user_id": jellyfin_user_id},
                    )
                    continue
                users_seen += 1
                min_last_played = user_cursors.get(jellyfin_user_id)
                max_remote_at: datetime | None = None
                try:
                    for raw in client.iter_user_items(
                        jellyfin_user_id,
                        min_last_played_date=min_last_played,
                    ):
                        event = jellyfin_item_to_event(
                            raw, connector_user_id=jellyfin_user_id
                        )
                        if event is not None:
                            events.append(event)
                            if event.remote_at is not None and (
                                max_remote_at is None or event.remote_at > max_remote_at
                            ):
                                max_remote_at = event.remote_at
                    user_max_remote_at[jellyfin_user_id] = max_remote_at
                except JellyfinError:
                    viewing_metric_inc("viewing_sync_user_fetch_errors")
                    log.warning(
                        "viewing_sync_user_fetch_error",
                        extra={"connector_user_id": jellyfin_user_id},
                    )
        return _FetchResult(
            events=events,
            users_seen=users_seen,
            user_max_remote_at=user_max_remote_at,
            users_missing=users_missing,
        )


class _FetchResult:
    __slots__ = ("events", "user_max_remote_at", "users_missing", "users_seen")

    def __init__(
        self,
        *,
        events: list[ExternalViewingEvent],
        users_seen: int,
        user_max_remote_at: dict[str, datetime | None] | None = None,
        users_missing: int = 0,
    ) -> None:
        self.events = events
        self.users_seen = users_seen
        self.user_max_remote_at = user_max_remote_at or {}
        self.users_missing = users_missing

"""Dry-run proposal engine — compares remote events to local read-only state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from miramedia.playback.completion import below_noise_floor, is_completed
from miramedia.playback.schemas import PlaybackProgress, WatchState
from miramedia.viewing_sync.schemas import (
    ExternalViewingEvent,
    MediaMatchResult,
    ProposalAction,
    ViewingProposal,
)

_CLOCK_SKEW = timedelta(seconds=5)
_FUTURE_SKEW = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class LocalViewingSnapshot:
    progress: PlaybackProgress | None
    watch: WatchState | None


def _parse_remote_at(remote_at: datetime | None) -> datetime | None:
    if remote_at is None:
        return None
    if remote_at.tzinfo is None:
        return remote_at.replace(tzinfo=UTC)
    return remote_at.astimezone(UTC)


def _remote_is_newer(
    remote_at: datetime | None,
    local_updated_at: datetime | None,
) -> bool:
    parsed = _parse_remote_at(remote_at)
    if parsed is None or local_updated_at is None:
        return True
    local = local_updated_at
    if local.tzinfo is None:
        local = local.replace(tzinfo=UTC)
    else:
        local = local.astimezone(UTC)
    return parsed > local + _CLOCK_SKEW


def _clock_skew_quarantine(remote_at: datetime | None) -> bool:
    parsed = _parse_remote_at(remote_at)
    if parsed is None:
        return False
    return parsed > datetime.now(UTC) + _FUTURE_SKEW


def build_proposal(
    event: ExternalViewingEvent,
    *,
    miramedia_user_id: UUID | None,
    match: MediaMatchResult,
    file_id: UUID | None,
    local: LocalViewingSnapshot,
    prior_digest: str | None = None,
) -> ViewingProposal:
    base = {
        "connector": event.connector,
        "connector_user_id": event.connector_user_id,
        "connector_item_id": event.connector_item_id,
        "miramedia_user_id": miramedia_user_id,
        "media_kind": match.media_kind,
        "media_id": match.media_id,
        "file_id": file_id,
        "match_confidence": match.confidence,
        "payload_digest": event.payload_digest,
        "position_ms": event.position_ms,
        "duration_ms": event.duration_ms,
        "remote_at": event.remote_at,
    }

    if _clock_skew_quarantine(event.remote_at):
        return ViewingProposal(
            action=ProposalAction.quarantine,
            reason="clock_skew",
            conflict_reason=None,
            completed=None,
            **base,
        )

    if local.watch is not None and local.watch.source == "manual":
        return ViewingProposal(
            action=ProposalAction.skip_manual,
            reason="manual_watch_state",
            conflict_reason="manual_precedence",
            completed=None,
            **base,
        )

    if prior_digest is not None and prior_digest == event.payload_digest:
        return ViewingProposal(
            action=ProposalAction.skip_no_op,
            reason="same_digest",
            conflict_reason=None,
            completed=None,
            **base,
        )

    completed = (
        is_completed(event.position_ms, event.duration_ms)
        if event.duration_ms > 0
        else event.remote_played
    )

    if (
        below_noise_floor(event.position_ms, completed=completed)
        and not event.remote_played
        and event.play_count <= 0
    ):
        return ViewingProposal(
            action=ProposalAction.skip_noise_floor,
            reason="below_noise_floor",
            conflict_reason=None,
            completed=completed,
            **base,
        )

    local_updated = local.progress.updated_at if local.progress is not None else None
    if local.progress is not None and not _remote_is_newer(
        event.remote_at, local_updated
    ):
        return ViewingProposal(
            action=ProposalAction.skip_local_newer,
            reason="local_newer",
            conflict_reason="local_precedence",
            completed=completed,
            **base,
        )

    if not event.remote_played and event.position_ms <= 0:
        actions: list[ProposalAction] = []
        if local.progress is not None:
            actions.append(ProposalAction.delete_progress)
        if local.watch is not None and local.watch.source == "derived":
            actions.append(ProposalAction.clear_derived_watched)
        action = (
            actions[0]
            if len(actions) == 1
            else ProposalAction.delete_progress
            if ProposalAction.delete_progress in actions
            else ProposalAction.clear_derived_watched
        )
        return ViewingProposal(
            action=action,
            reason="remote_unplayed",
            conflict_reason=None,
            completed=False,
            **base,
        )

    if completed:
        return ViewingProposal(
            action=ProposalAction.set_derived_watched,
            reason="remote_completed",
            conflict_reason=None,
            completed=True,
            **base,
        )

    return ViewingProposal(
        action=ProposalAction.upsert_progress,
        reason="remote_in_progress",
        conflict_reason=None,
        completed=False,
        **base,
    )

"""DB-free proposal tests for viewing-state dry-run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from miramedia.playback.schemas import PlaybackProgress, WatchState
from miramedia.viewing_sync.proposal import LocalViewingSnapshot, build_proposal
from miramedia.viewing_sync.schemas import (
    ExternalViewingEvent,
    MatchConfidence,
    MediaKind,
    MediaMatchResult,
    ProposalAction,
)


def _event(**overrides: object) -> ExternalViewingEvent:
    defaults: dict[str, object] = {
        "connector": "jellyfin",
        "connector_user_id": "jf-user",
        "connector_item_id": "jf-item",
        "media_kind": MediaKind.movie,
        "provider_ids": {"imdb": "tt123"},
        "season_number": None,
        "episode_number": None,
        "episode_number_end": None,
        "position_ms": 95_000,
        "duration_ms": 100_000,
        "remote_played": True,
        "remote_at": datetime.now(UTC),
        "payload_digest": "digest-1",
        "play_count": 1,
    }
    defaults.update(overrides)
    return ExternalViewingEvent(**defaults)  # type: ignore[arg-type]


def _match() -> MediaMatchResult:
    return MediaMatchResult(
        confidence=MatchConfidence.unique,
        media_kind=MediaKind.movie,
        media_id=uuid4(),
        candidate_ids=(uuid4(),),
    )


def test_manual_watch_state_skips_proposal() -> None:
    proposal = build_proposal(
        _event(),
        miramedia_user_id=uuid4(),
        match=_match(),
        file_id=uuid4(),
        local=LocalViewingSnapshot(
            progress=None,
            watch=WatchState(
                media_kind="movie",
                media_id=uuid4(),
                watched=True,
                source="manual",
                watched_at=datetime.now(UTC),
            ),
        ),
    )
    assert proposal.action == ProposalAction.skip_manual


def test_same_digest_is_no_op() -> None:
    proposal = build_proposal(
        _event(payload_digest="same"),
        miramedia_user_id=uuid4(),
        match=_match(),
        file_id=uuid4(),
        local=LocalViewingSnapshot(progress=None, watch=None),
        prior_digest="same",
    )
    assert proposal.action == ProposalAction.skip_no_op


def test_noise_floor_skip() -> None:
    proposal = build_proposal(
        _event(
            position_ms=1_000, duration_ms=100_000, remote_played=False, play_count=0
        ),
        miramedia_user_id=uuid4(),
        match=_match(),
        file_id=uuid4(),
        local=LocalViewingSnapshot(progress=None, watch=None),
    )
    assert proposal.action == ProposalAction.skip_noise_floor


def test_completed_threshold_proposes_derived_watched() -> None:
    proposal = build_proposal(
        _event(position_ms=95_000, duration_ms=100_000, remote_played=False),
        miramedia_user_id=uuid4(),
        match=_match(),
        file_id=uuid4(),
        local=LocalViewingSnapshot(progress=None, watch=None),
    )
    assert proposal.action == ProposalAction.set_derived_watched
    assert proposal.completed is True


def test_local_newer_skips() -> None:
    now = datetime.now(UTC)
    proposal = build_proposal(
        _event(remote_at=now - timedelta(minutes=5)),
        miramedia_user_id=uuid4(),
        match=_match(),
        file_id=uuid4(),
        local=LocalViewingSnapshot(
            progress=PlaybackProgress(
                file_id=uuid4(),
                media_kind="movie",
                position_ms=10_000,
                duration_ms=100_000,
                completed=False,
                updated_at=now,
            ),
            watch=None,
        ),
    )
    assert proposal.action == ProposalAction.skip_local_newer


def test_remote_unplayed_proposes_delete_progress() -> None:
    file_id = uuid4()
    proposal = build_proposal(
        _event(position_ms=0, duration_ms=100_000, remote_played=False),
        miramedia_user_id=uuid4(),
        match=_match(),
        file_id=file_id,
        local=LocalViewingSnapshot(
            progress=PlaybackProgress(
                file_id=file_id,
                media_kind="movie",
                position_ms=10_000,
                duration_ms=100_000,
                completed=False,
                updated_at=datetime.now(UTC) - timedelta(hours=1),
            ),
            watch=None,
        ),
    )
    assert proposal.action == ProposalAction.delete_progress

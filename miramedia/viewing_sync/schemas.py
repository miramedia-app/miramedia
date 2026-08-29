"""Connector-neutral external viewing-state records (design 386 §6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID


class MediaKind(StrEnum):
    movie = "movie"
    episode = "episode"


class QuarantineReason(StrEnum):
    unmapped_user = "unmapped_user"
    missing_provider_ids = "missing_provider_ids"
    zero_matches = "zero_matches"
    ambiguous_matches = "ambiguous_matches"
    multi_episode = "multi_episode"
    no_playable_file = "no_playable_file"
    clock_skew = "clock_skew"


class ProposalAction(StrEnum):
    upsert_progress = "upsert_progress"
    delete_progress = "delete_progress"
    set_derived_watched = "set_derived_watched"
    clear_derived_watched = "clear_derived_watched"
    skip_manual = "skip_manual"
    skip_noise_floor = "skip_noise_floor"
    skip_local_newer = "skip_local_newer"
    skip_no_op = "skip_no_op"
    quarantine = "quarantine"


class MatchConfidence(StrEnum):
    unique = "unique"
    ambiguous = "ambiguous"
    unmatched = "unmatched"


@dataclass(frozen=True, slots=True)
class ExternalViewingEvent:
    connector: str
    connector_user_id: str
    connector_item_id: str
    media_kind: MediaKind
    provider_ids: dict[str, str]
    season_number: int | None
    episode_number: int | None
    episode_number_end: int | None
    position_ms: int
    duration_ms: int
    remote_played: bool
    remote_at: datetime | None
    payload_digest: str
    title: str = ""
    year: int | None = None
    series_name: str | None = None
    play_count: int = 0


@dataclass(frozen=True, slots=True)
class MediaMatchResult:
    confidence: MatchConfidence
    media_kind: MediaKind | None = None
    media_id: UUID | None = None
    candidate_ids: tuple[UUID, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ViewingProposal:
    action: ProposalAction
    connector: str
    connector_user_id: str
    connector_item_id: str
    miramedia_user_id: UUID | None
    media_kind: MediaKind | None
    media_id: UUID | None
    file_id: UUID | None
    match_confidence: MatchConfidence | None
    reason: str | None
    conflict_reason: str | None
    payload_digest: str
    position_ms: int | None = None
    duration_ms: int | None = None
    completed: bool | None = None
    remote_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    reason: QuarantineReason
    connector_user_id: str
    connector_item_id: str
    item_type: str
    provider_ids: dict[str, str]
    candidate_mira_ids: tuple[UUID, ...] = ()
    title: str = ""
    year: int | None = None
    series_name: str | None = None
    season: int | None = None
    episode: int | None = None


@dataclass
class DryRunMetrics:
    users_mapped: int = 0
    users_seen: int = 0
    items_seen: int = 0
    items_with_play_signal: int = 0
    unique_matches: int = 0
    ambiguous_count: int = 0
    unmatched_with_ids: int = 0
    unmatched_without_ids: int = 0
    manual_block_count: int = 0
    skipped_local_newer: int = 0
    skipped_noise_floor: int = 0
    skipped_no_op: int = 0
    proposed_progress_upserts: int = 0
    proposed_progress_deletes: int = 0
    proposed_watched_sets: int = 0
    proposed_watched_clears: int = 0
    multi_episode_quarantine: int = 0
    quarantine_count: int = 0
    errors: int = 0

    def record_proposal(self, proposal: ViewingProposal) -> None:
        if proposal.action == ProposalAction.skip_manual:
            self.manual_block_count += 1
        elif proposal.action == ProposalAction.skip_local_newer:
            self.skipped_local_newer += 1
        elif proposal.action == ProposalAction.skip_noise_floor:
            self.skipped_noise_floor += 1
        elif proposal.action == ProposalAction.skip_no_op:
            self.skipped_no_op += 1
        elif proposal.action == ProposalAction.upsert_progress:
            self.proposed_progress_upserts += 1
        elif proposal.action == ProposalAction.delete_progress:
            self.proposed_progress_deletes += 1
        elif proposal.action == ProposalAction.set_derived_watched:
            self.proposed_watched_sets += 1
        elif proposal.action == ProposalAction.clear_derived_watched:
            self.proposed_watched_clears += 1
        elif proposal.action == ProposalAction.quarantine:
            self.quarantine_count += 1

    def record_quarantine(self, record: QuarantineRecord) -> None:
        self.quarantine_count += 1
        if record.reason == QuarantineReason.multi_episode:
            self.multi_episode_quarantine += 1
        if record.reason == QuarantineReason.ambiguous_matches:
            self.ambiguous_count += 1
        if record.reason == QuarantineReason.zero_matches and record.provider_ids:
            self.unmatched_with_ids += 1
        if (
            record.reason
            in {
                QuarantineReason.zero_matches,
                QuarantineReason.missing_provider_ids,
            }
            and not record.provider_ids
        ):
            self.unmatched_without_ids += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "users_mapped": self.users_mapped,
            "users_seen": self.users_seen,
            "items_seen": self.items_seen,
            "items_with_play_signal": self.items_with_play_signal,
            "unique_matches": self.unique_matches,
            "ambiguous_count": self.ambiguous_count,
            "unmatched_with_ids": self.unmatched_with_ids,
            "unmatched_without_ids": self.unmatched_without_ids,
            "manual_block_count": self.manual_block_count,
            "skipped_local_newer": self.skipped_local_newer,
            "skipped_noise_floor": self.skipped_noise_floor,
            "skipped_no_op": self.skipped_no_op,
            "proposed_progress_upserts": self.proposed_progress_upserts,
            "proposed_progress_deletes": self.proposed_progress_deletes,
            "proposed_watched_sets": self.proposed_watched_sets,
            "proposed_watched_clears": self.proposed_watched_clears,
            "multi_episode_quarantine": self.multi_episode_quarantine,
            "quarantine_count": self.quarantine_count,
            "errors": self.errors,
        }


ConnectorName = Literal["jellyfin"]

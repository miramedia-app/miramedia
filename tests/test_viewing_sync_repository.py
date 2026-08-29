"""DB-free tests for viewing-sync bulk reads and batch persistence (plan 446)."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from miramedia.playback.bulk import BULK_CHUNK_SIZE as _BULK_CHUNK_SIZE
from miramedia.playback.bulk import UserMediaKey
from miramedia.playback.models import WatchStateSource
from miramedia.playback.repository import PlaybackRepository, UserFileKey
from miramedia.playback.schemas import MediaKind as PlaybackMediaKind
from miramedia.playback.schemas import PlaybackProgress, WatchState
from miramedia.viewing_sync.files import PlayableFile, bulk_pick_playable_files
from miramedia.viewing_sync.repository import (
    ConnectorItemKey,
    ViewingSyncRepository,
)
from miramedia.viewing_sync.schemas import (
    ExternalViewingEvent,
    MatchConfidence,
    MediaKind,
    MediaMatchResult,
    ProposalAction,
    QuarantineReason,
    QuarantineRecord,
    ViewingProposal,
)

_PLAYBACK_ROOT = Path(__file__).resolve().parents[1] / "miramedia" / "playback"


def _viewing_sync_imports_in_playback() -> list[str]:
    offenders: list[str] = []
    for path in sorted(_PLAYBACK_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_PLAYBACK_ROOT.parent.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{rel}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("miramedia.viewing_sync")
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("miramedia.viewing_sync"):
                    offenders.append(f"{rel}: from {node.module} import ...")
    return offenders


def test_playback_package_does_not_import_viewing_sync() -> None:
    assert _viewing_sync_imports_in_playback() == []


def _run(coro):
    return asyncio.run(coro)


def _event(**overrides: object) -> ExternalViewingEvent:
    defaults: dict[str, object] = {
        "connector": "jellyfin",
        "connector_user_id": "jf-user",
        "connector_item_id": "jf-item",
        "media_kind": MediaKind.movie,
        "provider_ids": {"Imdb": "tt123"},
        "season_number": None,
        "episode_number": None,
        "episode_number_end": None,
        "position_ms": 95_000,
        "duration_ms": 100_000,
        "remote_played": True,
        "remote_at": datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        "payload_digest": "digest-1",
        "play_count": 1,
    }
    defaults.update(overrides)
    return ExternalViewingEvent(**defaults)  # type: ignore[arg-type]


def _proposal(**overrides: object) -> ViewingProposal:
    defaults: dict[str, object] = {
        "action": ProposalAction.set_derived_watched,
        "connector": "jellyfin",
        "connector_user_id": "jf-user",
        "connector_item_id": "jf-item",
        "miramedia_user_id": uuid4(),
        "media_kind": MediaKind.movie,
        "media_id": uuid4(),
        "file_id": uuid4(),
        "reason": "remote_completed",
        "match_confidence": MatchConfidence.unique,
        "conflict_reason": None,
        "payload_digest": "digest-1",
        "position_ms": 95_000,
        "duration_ms": 100_000,
        "completed": True,
        "remote_at": datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return ViewingProposal(**defaults)  # type: ignore[arg-type]


class _CountingSession:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.flush_calls = 0
        self.prior_rows: list[tuple[str, str, str, str, datetime]] = []
        self.movie_file_rows: list[tuple[object, object | None]] = []
        self.progress_rows: list[object] = []
        self.watch_rows: list[object] = []
        self.completed_pairs: list[tuple[UUID, UUID]] = []

    async def execute(self, stmt) -> SimpleNamespace | _ScalarRows:
        self.execute_calls += 1
        compiled = str(stmt).upper()
        compiled_lower = compiled.lower()
        if "VIEWING_SYNC_PROPOSAL.PAYLOAD_DIGEST" in compiled:
            return SimpleNamespace(all=lambda: self.prior_rows)
        if "DISTINCT" in compiled and "COMPLETED" in compiled:
            return SimpleNamespace(all=lambda: self.completed_pairs)
        if (
            "JOIN" in compiled
            and "MOVIE_FILE" in compiled
            and "PLAYBACK_PROGRESS" in compiled
        ):
            return SimpleNamespace(all=lambda: self.movie_file_rows)
        if "PLAYBACK_PROGRESS" in compiled and "JOIN" not in compiled:
            return _ScalarRows(self.progress_rows)
        if "MEDIA_WATCH_STATE" in compiled:
            return _ScalarRows(self.watch_rows)
        msg = f"unexpected statement: {compiled_lower}"
        raise AssertionError(msg)

    async def flush(self) -> None:
        self.flush_calls += 1

    def add(self, _row: object) -> None:
        return None


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarRows:
        return self

    def all(self) -> list[object]:
        return self._rows


def test_bulk_get_prior_digests_returns_latest_per_connector_item() -> None:
    session = _CountingSession()
    session.prior_rows = [
        (
            "jellyfin",
            "user-a",
            "item-1",
            "digest-old",
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
        (
            "jellyfin",
            "user-a",
            "item-1",
            "digest-new",
            datetime(2026, 2, 1, tzinfo=UTC),
        ),
    ]
    repo = ViewingSyncRepository(session)  # type: ignore[arg-type]
    key = ConnectorItemKey("jellyfin", "user-a", "item-1")

    digests = _run(repo.bulk_get_prior_digests([key]))

    assert digests == {key: "digest-new"}
    assert session.execute_calls == 1


def test_bulk_get_prior_digests_chunks_large_key_sets() -> None:
    session = _CountingSession()
    repo = ViewingSyncRepository(session)  # type: ignore[arg-type]
    keys = [
        ConnectorItemKey("jellyfin", f"user-{index}", f"item-{index}")
        for index in range(_BULK_CHUNK_SIZE + 1)
    ]

    _run(repo.bulk_get_prior_digests(keys))

    assert session.execute_calls == 2


def test_insert_proposals_batch_flushes_once_per_chunk() -> None:
    session = _CountingSession()
    repo = ViewingSyncRepository(session)  # type: ignore[arg-type]
    proposals = [_proposal(connector_item_id=f"item-{index}") for index in range(250)]

    inserted = _run(repo.insert_proposals_batch(uuid4(), proposals))

    assert inserted == 250
    assert session.flush_calls == 2


def test_insert_quarantines_batch_flushes_once_per_chunk() -> None:
    session = _CountingSession()
    repo = ViewingSyncRepository(session)  # type: ignore[arg-type]
    records = [
        QuarantineRecord(
            reason=QuarantineReason.zero_matches,
            connector_user_id="jf-user",
            connector_item_id=f"item-{index}",
            item_type="movie",
            provider_ids={"Imdb": "tt999"},
        )
        for index in range(_BULK_CHUNK_SIZE + 5)
    ]

    _run(repo.insert_quarantines_batch(uuid4(), records))

    assert session.flush_calls == 2


def test_bulk_pick_playable_files_prefers_in_progress_file() -> None:
    user_id = uuid4()
    movie_id = uuid4()
    older_file_id = uuid4()
    newer_file_id = uuid4()
    key = UserMediaKey(
        user_id=user_id,
        media_kind=PlaybackMediaKind.movie,
        media_id=movie_id,
    )

    older_file = SimpleNamespace(
        id=older_file_id,
        movie_id=movie_id,
        imported_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer_file = SimpleNamespace(
        id=newer_file_id,
        movie_id=movie_id,
        imported_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    in_progress = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        updated_at=datetime(2026, 1, 15, tzinfo=UTC),
        completed=False,
    )

    session = _CountingSession()
    session.movie_file_rows = [
        (newer_file, None),
        (older_file, in_progress),
    ]
    picked = _run(bulk_pick_playable_files(session, [key]))  # type: ignore[arg-type]

    assert picked[key] == PlayableFile(
        file_id=older_file_id,
        media_kind=PlaybackMediaKind.movie,
    )
    assert session.execute_calls == 1


def test_bulk_pick_playable_files_returns_empty_for_absent_files() -> None:
    session = _CountingSession()
    key = UserMediaKey(
        user_id=uuid4(),
        media_kind=PlaybackMediaKind.movie,
        media_id=uuid4(),
    )

    picked = _run(bulk_pick_playable_files(session, [key]))  # type: ignore[arg-type]

    assert picked == {}


def test_bulk_get_progress_maps_user_file_keys() -> None:
    user_id = uuid4()
    file_id = uuid4()
    key = UserFileKey(
        user_id=user_id,
        file_id=file_id,
        media_kind=PlaybackMediaKind.movie,
    )
    row = SimpleNamespace(
        user_id=user_id,
        movie_file_id=file_id,
        episode_file_id=None,
        position_ms=10_000,
        duration_ms=100_000,
        completed=False,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = _CountingSession()
    session.progress_rows = [row]
    repo = PlaybackRepository(session)  # type: ignore[arg-type]

    progress = _run(repo.bulk_get_progress([key]))

    assert progress[key] == PlaybackProgress(
        file_id=file_id,
        media_kind=PlaybackMediaKind.movie,
        position_ms=10_000,
        duration_ms=100_000,
        completed=False,
        updated_at=row.updated_at,
    )


def test_bulk_get_watched_prefers_manual_over_completed_progress() -> None:
    user_id = uuid4()
    media_id = uuid4()
    key = UserMediaKey(
        user_id=user_id,
        media_kind=PlaybackMediaKind.movie,
        media_id=media_id,
    )
    session = _CountingSession()
    session.watch_rows = [
        SimpleNamespace(
            user_id=user_id,
            movie_id=media_id,
            episode_id=None,
            watched=False,
            source=WatchStateSource.manual,
            watched_at=datetime(2026, 1, 2, tzinfo=UTC),
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    ]
    session.completed_pairs = [(user_id, media_id)]
    repo = PlaybackRepository(session)  # type: ignore[arg-type]

    watched = _run(repo.bulk_get_watched([key]))

    assert watched[key] == WatchState(
        media_kind="movie",
        media_id=media_id,
        watched=False,
        source="manual",
        watched_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def test_bulk_get_watched_uses_completed_progress_when_no_manual_row() -> None:
    user_id = uuid4()
    media_id = uuid4()
    key = UserMediaKey(
        user_id=user_id,
        media_kind=PlaybackMediaKind.movie,
        media_id=media_id,
    )
    session = _CountingSession()
    session.completed_pairs = [(user_id, media_id)]
    repo = PlaybackRepository(session)  # type: ignore[arg-type]

    watched = _run(repo.bulk_get_watched([key]))

    assert watched[key].watched is True
    assert watched[key].source == "derived"


@pytest.mark.anyio
async def test_repeated_connector_items_second_is_no_op_digest() -> None:
    from miramedia.viewing_sync.proposal import LocalViewingSnapshot, build_proposal

    media_id = uuid4()
    match = MediaMatchResult(
        confidence=MatchConfidence.unique,
        media_kind=MediaKind.movie,
        media_id=media_id,
        candidate_ids=(media_id,),
    )
    file_id = uuid4()
    local = LocalViewingSnapshot(progress=None, watch=None)
    first = build_proposal(
        _event(connector_item_id="dup-item", payload_digest="same-digest"),
        miramedia_user_id=uuid4(),
        match=match,
        file_id=file_id,
        local=local,
        prior_digest=None,
    )
    second = build_proposal(
        _event(connector_item_id="dup-item", payload_digest="same-digest"),
        miramedia_user_id=uuid4(),
        match=match,
        file_id=file_id,
        local=local,
        prior_digest=first.payload_digest,
    )

    assert first.action == ProposalAction.set_derived_watched
    assert second.action == ProposalAction.skip_no_op

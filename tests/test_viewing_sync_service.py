"""Service tests — dry-run must not mutate playback repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from miramedia.playback.bulk import UserMediaKey
from miramedia.playback.schemas import MediaKind as PlaybackMediaKind
from miramedia.viewing_sync.files import PlayableFile
from miramedia.viewing_sync.jellyfin.client import JellyfinError
from miramedia.viewing_sync.matcher import MediaCatalog, media_catalog_from_sequences
from miramedia.viewing_sync.schemas import (
    DryRunMetrics,
    ExternalViewingEvent,
    MediaKind,
    ProposalAction,
    QuarantineReason,
)
from miramedia.viewing_sync.service import ViewingSyncDryRunService


@dataclass
class _Movie:
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


@dataclass
class _Show:
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


@dataclass
class _Season:
    id: UUID
    show_id: UUID
    number: int


@dataclass
class _Episode:
    id: UUID
    season_id: UUID
    number: int


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


def _movie(imdb: str = "tt123") -> _Movie:
    return _Movie(
        id=uuid4(),
        imdb_id=imdb,
        external_id=imdb,
        metadata_provider="native",
    )


def _empty_catalog() -> MediaCatalog:
    return media_catalog_from_sequences([], [], {})


def _movie_only_catalog(movie: _Movie) -> MediaCatalog:
    return media_catalog_from_sequences([movie], [], {})


def _episode_catalog(
    show: _Show,
    *,
    season_number: int,
    episode_number: int,
) -> tuple[_Show, MediaCatalog, _Episode]:
    season = _Season(id=uuid4(), show_id=show.id, number=season_number)
    episode = _Episode(id=uuid4(), season_id=season.id, number=episode_number)
    catalog = media_catalog_from_sequences(
        [],
        [show],
        {(show.id, season_number): [episode]},
    )
    return show, catalog, episode


def _service_with_playback_mocks() -> ViewingSyncDryRunService:
    db = AsyncMock()
    service = ViewingSyncDryRunService(db)
    service.playback = MagicMock()
    service.playback.upsert_progress = AsyncMock()
    service.playback.delete_progress = AsyncMock()
    service.playback.set_watched = AsyncMock()
    service.playback.delete_watched = AsyncMock()
    service.playback.delete_all_progress = AsyncMock()
    service.playback.delete_all_viewing_state = AsyncMock()
    service.playback.get_progress = AsyncMock(return_value=None)
    service.playback.get_watched = AsyncMock(
        return_value=MagicMock(source=None, watched=False)
    )
    service.playback.bulk_get_progress = AsyncMock(return_value={})
    service.playback.bulk_get_watched = AsyncMock(return_value={})
    _mock_batch_persistence(service)
    return service


def _mock_batch_persistence(service: ViewingSyncDryRunService) -> None:
    service.repository.bulk_get_prior_digests = AsyncMock(return_value={})
    service.repository.insert_proposals_batch = AsyncMock()
    service.repository.insert_quarantines_batch = AsyncMock()


def _assert_playback_mutators_unused(service: ViewingSyncDryRunService) -> None:
    service.playback.upsert_progress.assert_not_called()
    service.playback.delete_progress.assert_not_called()
    service.playback.set_watched.assert_not_called()
    service.playback.delete_watched.assert_not_called()
    service.playback.delete_all_progress.assert_not_called()
    service.playback.delete_all_viewing_state.assert_not_called()


def _mock_user_cursors(
    service: ViewingSyncDryRunService,
    cursors: dict[str, datetime | None],
) -> AsyncMock:
    get_user_cursors = AsyncMock(
        side_effect=lambda _connector, user_ids: {
            uid: cursors.get(uid) for uid in user_ids
        }
    )
    service.repository.get_user_cursors = get_user_cursors
    service.repository.set_user_cursor = AsyncMock()
    return service.repository.set_user_cursor


def _enabled_config(
    cfg: MagicMock,
    *,
    user_id: UUID,
    jellyfin_user: str = "jf-user",
    user_map: dict[str, str] | None = None,
) -> None:
    cfg.return_value.viewing_sync.enabled = True
    cfg.return_value.viewing_sync.jellyfin.user_map = (
        {jellyfin_user: str(user_id)} if user_map is None else user_map
    )
    cfg.return_value.viewing_sync.jellyfin.api_key = "test-api-key"
    cfg.return_value.viewing_sync.retention_days = 14
    cfg.return_value.viewing_sync.retention_min_rows = 5000


@pytest.mark.anyio
async def test_poll_once_loads_catalog_once_regardless_of_event_count() -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()
    catalog_loads = 0

    async def _counting_load() -> MediaCatalog:
        nonlocal catalog_loads
        catalog_loads += 1
        return _empty_catalog()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)
    service.repository.load_media_catalog = AsyncMock(side_effect=_counting_load)

    events = [
        _event(connector_item_id=f"item-{index}", provider_ids={"Imdb": "tt999"})
        for index in range(100)
    ]

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=events, users_seen=1),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    assert metrics is not None
    assert catalog_loads == 1
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_large_unique_match_page_uses_bounded_bulk_reads() -> None:
    user_id = uuid4()
    movies = [_movie(f"tt{index:03d}") for index in range(100)]
    catalog = media_catalog_from_sequences(movies, [], {})
    service = _service_with_playback_mocks()
    run_id = uuid4()
    execute_calls = 0
    flush_calls = 0

    async def counting_execute(_stmt: object) -> MagicMock:
        nonlocal execute_calls
        execute_calls += 1
        return MagicMock(all=list)

    service.db.execute = counting_execute

    async def counting_flush() -> None:
        nonlocal flush_calls
        flush_calls += 1

    service.db.flush = counting_flush

    playables = {
        UserMediaKey(
            user_id=user_id,
            media_kind=PlaybackMediaKind.movie,
            media_id=movie.id,
        ): PlayableFile(
            file_id=uuid4(),
            media_kind=PlaybackMediaKind.movie,
        )
        for movie in movies
    }

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)

    events = [
        _event(
            connector_item_id=f"item-{index}",
            provider_ids={"Imdb": f"tt{index:03d}"},
        )
        for index in range(100)
    ]

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=catalog),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=events, users_seen=1),
        ),
        patch(
            "miramedia.viewing_sync.service.bulk_pick_playable_files",
            new=AsyncMock(return_value=playables),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    assert metrics is not None
    assert metrics.unique_matches == 100
    assert execute_calls <= 12
    assert flush_calls <= 2
    service.repository.insert_proposals_batch.assert_called_once()
    assert len(service.repository.insert_proposals_batch.await_args.args[1]) == 100


@pytest.mark.anyio
async def test_poll_once_repeated_connector_item_second_is_no_op() -> None:
    user_id = uuid4()
    movie = _movie()
    service = _service_with_playback_mocks()
    run_id = uuid4()
    file_id = uuid4()
    media_key = UserMediaKey(
        user_id=user_id,
        media_kind=PlaybackMediaKind.movie,
        media_id=movie.id,
    )
    digest = "stable-digest"

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_movie_only_catalog(movie)),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[
                    _event(
                        connector_item_id="dup-item",
                        payload_digest=digest,
                        provider_ids={"Imdb": "tt123"},
                    ),
                    _event(
                        connector_item_id="dup-item",
                        payload_digest=digest,
                        provider_ids={"Imdb": "tt123"},
                    ),
                ],
                users_seen=1,
            ),
        ),
        patch(
            "miramedia.viewing_sync.service.bulk_pick_playable_files",
            new=AsyncMock(
                return_value={
                    media_key: PlayableFile(
                        file_id=file_id,
                        media_kind=PlaybackMediaKind.movie,
                    )
                }
            ),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    proposals = service.repository.insert_proposals_batch.await_args.args[1]
    assert metrics is not None
    assert metrics.unique_matches == 2
    assert proposals[0].action == ProposalAction.set_derived_watched
    assert proposals[1].action == ProposalAction.skip_no_op


@pytest.mark.anyio
async def test_load_media_catalog_issues_bounded_queries() -> None:
    from miramedia.viewing_sync.repository import ViewingSyncRepository

    execute_calls = 0

    async def counting_execute(_stmt: object) -> MagicMock:
        nonlocal execute_calls
        execute_calls += 1
        return MagicMock(all=list)

    db = AsyncMock()
    db.execute = counting_execute
    repository = ViewingSyncRepository(db)

    catalog = await repository.load_media_catalog()

    assert execute_calls == 4
    assert catalog.movies.by_imdb == {}
    assert catalog.shows.by_imdb == {}
    assert catalog.episodes.episodes_by_show_season_episode == {}


@pytest.mark.anyio
async def test_poll_once_disabled_returns_none() -> None:
    db = AsyncMock()
    service = ViewingSyncDryRunService(db)
    with patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg:
        cfg.return_value.viewing_sync.enabled = False
        assert await service.poll_once() is None


@pytest.mark.anyio
async def test_poll_once_invalid_user_map_does_not_start_run() -> None:
    service = _service_with_playback_mocks()
    service.repository.start_run = AsyncMock()
    service.repository.finish_run = AsyncMock()

    with patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg:
        _enabled_config(cfg, user_id=uuid4(), user_map={"jf-user": "not-a-uuid"})
        metrics = await service.poll_once()

    assert metrics is None
    service.repository.start_run.assert_not_called()
    service.repository.finish_run.assert_not_called()


@pytest.mark.anyio
async def test_poll_once_empty_user_map_does_not_start_run() -> None:
    service = _service_with_playback_mocks()
    service.repository.start_run = AsyncMock()
    service.repository.finish_run = AsyncMock()

    with patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg:
        _enabled_config(cfg, user_id=uuid4(), user_map={})
        metrics = await service.poll_once()

    assert metrics is None
    service.repository.start_run.assert_not_called()
    service.repository.finish_run.assert_not_called()


@pytest.mark.anyio
async def test_poll_once_never_mutates_playback_repository() -> None:
    db = AsyncMock()
    service = ViewingSyncDryRunService(db)
    service.playback = MagicMock()
    service.playback.upsert_progress = AsyncMock()
    service.playback.delete_progress = AsyncMock()
    service.playback.set_watched = AsyncMock()
    service.playback.delete_watched = AsyncMock()
    service.playback.delete_all_progress = AsyncMock()
    service.playback.delete_all_viewing_state = AsyncMock()
    service.playback.get_progress = AsyncMock(return_value=None)
    service.playback.get_watched = AsyncMock(
        return_value=MagicMock(source=None, watched=False)
    )
    _mock_batch_persistence(service)

    user_id = uuid4()
    jellyfin_user = str(uuid4())
    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=[], users_seen=1),
        ),
    ):
        _enabled_config(cfg, user_id=user_id, jellyfin_user=jellyfin_user)
        service.repository.start_run = AsyncMock(return_value=MagicMock(id=uuid4()))
        service.repository.finish_run = AsyncMock()
        _mock_user_cursors(service, {"jf-user": None})
        service.repository.purge_stale_rows = AsyncMock()
        await service.poll_once()

    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_records_unique_match_proposal_without_playback_writes() -> (
    None
):
    user_id = uuid4()
    movie = _movie()
    service = _service_with_playback_mocks()
    run_id = uuid4()
    file_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)

    media_key = UserMediaKey(
        user_id=user_id,
        media_kind=PlaybackMediaKind.movie,
        media_id=movie.id,
    )

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_movie_only_catalog(movie)),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[_event(provider_ids={"Imdb": "tt123"})],
                users_seen=1,
            ),
        ),
        patch(
            "miramedia.viewing_sync.service.bulk_pick_playable_files",
            new=AsyncMock(
                return_value={
                    media_key: PlayableFile(
                        file_id=file_id,
                        media_kind=PlaybackMediaKind.movie,
                    )
                }
            ),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    assert metrics is not None
    assert metrics.unique_matches == 1
    assert metrics.proposed_watched_sets == 1
    service.repository.insert_proposals_batch.assert_called_once()
    proposals = service.repository.insert_proposals_batch.await_args.args[1]
    assert len(proposals) == 1
    assert proposals[0].action == ProposalAction.set_derived_watched
    service.repository.finish_run.assert_called_once_with(
        run_id,
        status="success",
        metrics=metrics.to_dict(),
        error_redacted=None,
    )
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_overrides", "catalog_factory", "expected_reason"),
    [
        (
            {"connector_user_id": "unknown-user"},
            lambda: _empty_catalog(),
            QuarantineReason.unmapped_user,
        ),
        (
            {"provider_ids": {}},
            lambda: _movie_only_catalog(_movie()),
            QuarantineReason.missing_provider_ids,
        ),
        (
            {"provider_ids": {"Imdb": "tt999"}},
            lambda: _movie_only_catalog(_movie("tt123")),
            QuarantineReason.zero_matches,
        ),
        (
            {"provider_ids": {"Imdb": "tt123"}},
            lambda: media_catalog_from_sequences(
                [_movie("tt123"), _movie("tt123")], [], {}
            ),
            QuarantineReason.ambiguous_matches,
        ),
        (
            {
                "media_kind": MediaKind.episode,
                "provider_ids": {"Imdb": "tt999"},
                "season_number": 1,
                "episode_number": 1,
                "episode_number_end": 2,
            },
            lambda: _episode_catalog(
                _Show(
                    id=uuid4(),
                    imdb_id="tt999",
                    external_id="tt999",
                    metadata_provider="native",
                ),
                season_number=1,
                episode_number=1,
            )[1],
            QuarantineReason.multi_episode,
        ),
    ],
)
async def test_poll_once_quarantines_match_failures(
    event_overrides: dict[str, object],
    catalog_factory: object,
    expected_reason: QuarantineReason,
) -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()
    catalog = catalog_factory()  # type: ignore[operator,misc]

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=catalog),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[_event(**event_overrides)],
                users_seen=1,
            ),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    assert metrics is not None
    assert metrics.quarantine_count == 1
    records = service.repository.insert_quarantines_batch.await_args.args[1]
    assert records[0].reason == expected_reason
    service.repository.insert_proposals_batch.assert_called_once_with(run_id, [])
    service.repository.finish_run.assert_called_once_with(
        run_id,
        status="success",
        metrics=metrics.to_dict(),
        error_redacted=None,
    )
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_quarantines_no_playable_file() -> None:
    user_id = uuid4()
    movie = _movie()
    service = _service_with_playback_mocks()
    run_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_movie_only_catalog(movie)),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=[_event()], users_seen=1),
        ),
        patch(
            "miramedia.viewing_sync.service.bulk_pick_playable_files",
            new=AsyncMock(return_value={}),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    records = service.repository.insert_quarantines_batch.await_args.args[1]
    assert records[0].reason == QuarantineReason.no_playable_file
    assert metrics is not None
    assert metrics.quarantine_count == 1
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_quarantines_clock_skew() -> None:
    user_id = uuid4()
    movie = _movie()
    service = _service_with_playback_mocks()
    run_id = uuid4()
    file_id = uuid4()
    future_remote_at = datetime.now(UTC) + timedelta(hours=2)

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()
    _mock_batch_persistence(service)

    media_key = UserMediaKey(
        user_id=user_id,
        media_kind=PlaybackMediaKind.movie,
        media_id=movie.id,
    )

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_movie_only_catalog(movie)),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[_event(remote_at=future_remote_at)],
                users_seen=1,
            ),
        ),
        patch(
            "miramedia.viewing_sync.service.bulk_pick_playable_files",
            new=AsyncMock(
                return_value={
                    media_key: PlayableFile(
                        file_id=file_id,
                        media_kind=PlaybackMediaKind.movie,
                    )
                }
            ),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    records = service.repository.insert_quarantines_batch.await_args.args[1]
    assert records[0].reason == QuarantineReason.clock_skew
    assert metrics is not None
    assert metrics.quarantine_count == 1
    service.repository.insert_proposals_batch.assert_called_once_with(run_id, [])
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_advances_cursor_only_after_successful_event_fetch() -> None:
    user_id = uuid4()
    remote_at = datetime(2026, 2, 1, 8, 30, tzinfo=UTC)
    service = _service_with_playback_mocks()
    run_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    set_user_cursor = _mock_user_cursors(
        service, {"jf-user": datetime(2026, 1, 1, tzinfo=UTC)}
    )
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[_event(remote_at=remote_at)],
                users_seen=1,
                user_max_remote_at={"jf-user": remote_at},
            ),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        await service.poll_once()

    set_user_cursor.assert_called_once_with("jellyfin", "jf-user", remote_at)


@pytest.mark.anyio
async def test_poll_once_does_not_advance_cursor_without_events() -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    set_user_cursor = _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=[], users_seen=1),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        await service.poll_once()

    set_user_cursor.assert_not_called()


@pytest.mark.anyio
async def test_poll_once_purges_stale_rows_with_retention_config() -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=[], users_seen=1),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        cfg.return_value.viewing_sync.retention_days = 21
        cfg.return_value.viewing_sync.retention_min_rows = 1234
        await service.poll_once()

    service.repository.purge_stale_rows.assert_called_once_with(
        retention_days=21,
        retention_min_rows=1234,
    )


@pytest.mark.anyio
async def test_poll_once_redacts_jellyfin_error_and_finishes_run() -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()
    api_key = "test-api-key"

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            side_effect=JellyfinError(f"upstream failed token={api_key}"),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        cfg.return_value.viewing_sync.jellyfin.api_key = api_key
        metrics = await service.poll_once()

    assert metrics is not None
    assert metrics.errors == 1
    finish_kwargs = service.repository.finish_run.await_args.kwargs
    assert finish_kwargs["status"] == "error"
    assert finish_kwargs["error_redacted"] is not None
    assert api_key not in finish_kwargs["error_redacted"]
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_finishes_run_on_unexpected_exception() -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            side_effect=RuntimeError("catalog exploded"),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    assert metrics is not None
    assert metrics.errors == 1
    service.repository.finish_run.assert_called_once()
    assert service.repository.finish_run.await_args.kwargs["status"] == "error"
    assert service.repository.finish_run.await_args.kwargs["error_redacted"] is None
    _assert_playback_mutators_unused(service)


@pytest.mark.anyio
async def test_poll_once_success_path_finishes_run() -> None:
    user_id = uuid4()
    service = _service_with_playback_mocks()
    run_id = uuid4()

    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    _mock_user_cursors(service, {"jf-user": None})
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(events=[], users_seen=1),
        ),
    ):
        _enabled_config(cfg, user_id=user_id)
        metrics = await service.poll_once()

    assert isinstance(metrics, DryRunMetrics)
    service.repository.finish_run.assert_called_once_with(
        run_id,
        status="success",
        metrics=metrics.to_dict(),
        error_redacted=None,
    )


@pytest.mark.anyio
async def test_poll_once_advances_cursors_independently_per_user() -> None:
    user_a = uuid4()
    user_b = uuid4()
    jf_user_a = "jf-user-a"
    jf_user_b = "jf-user-b"
    remote_a = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    remote_b = datetime(2026, 2, 20, 8, 0, tzinfo=UTC)
    cursor_a = datetime(2026, 1, 1, tzinfo=UTC)
    cursor_b = datetime(2026, 2, 1, tzinfo=UTC)

    service = _service_with_playback_mocks()
    run_id = uuid4()
    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    set_user_cursor = _mock_user_cursors(
        service, {jf_user_a: cursor_a, jf_user_b: cursor_b}
    )
    service.repository.purge_stale_rows = AsyncMock()

    fetch_mock = MagicMock(
        return_value=_FetchResult(
            events=[
                _event(connector_user_id=jf_user_a, remote_at=remote_a),
                _event(connector_user_id=jf_user_b, remote_at=remote_b),
            ],
            users_seen=2,
            user_max_remote_at={jf_user_a: remote_a, jf_user_b: remote_b},
        )
    )

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(service, "_fetch_jellyfin_events", fetch_mock),
    ):
        _enabled_config(
            cfg,
            user_id=user_a,
            user_map={jf_user_a: str(user_a), jf_user_b: str(user_b)},
        )
        await service.poll_once()

    fetch_mock.assert_called_once()
    passed_cursors = fetch_mock.call_args.args[-1]
    assert passed_cursors[jf_user_a] == cursor_a
    assert passed_cursors[jf_user_b] == cursor_b
    assert set_user_cursor.await_args_list == [
        (("jellyfin", jf_user_a, remote_a),),
        (("jellyfin", jf_user_b, remote_b),),
    ]


@pytest.mark.anyio
async def test_poll_once_late_added_user_gets_null_cursor() -> None:
    user_a = uuid4()
    user_b = uuid4()
    jf_user_a = "jf-user-a"
    jf_user_b = "jf-user-b"
    existing_cursor = datetime(2026, 3, 1, tzinfo=UTC)

    service = _service_with_playback_mocks()
    run_id = uuid4()
    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    get_user_cursors = AsyncMock(
        side_effect=lambda _connector, _user_ids: {
            jf_user_a: existing_cursor,
            jf_user_b: None,
        }
    )
    service.repository.get_user_cursors = get_user_cursors
    service.repository.set_user_cursor = AsyncMock()
    service.repository.purge_stale_rows = AsyncMock()

    fetch_mock = MagicMock(
        return_value=_FetchResult(events=[], users_seen=2, user_max_remote_at={})
    )

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(service, "_fetch_jellyfin_events", fetch_mock),
    ):
        _enabled_config(
            cfg,
            user_id=user_a,
            user_map={jf_user_a: str(user_a), jf_user_b: str(user_b)},
        )
        await service.poll_once()

    passed_cursors = fetch_mock.call_args.args[-1]
    assert passed_cursors[jf_user_a] == existing_cursor
    assert passed_cursors[jf_user_b] is None


@pytest.mark.anyio
async def test_poll_once_absent_user_retains_cursor() -> None:
    user_a = uuid4()
    user_b = uuid4()
    jf_user_a = "jf-user-a"
    jf_user_b = "jf-user-b"
    cursor_b = datetime(2026, 2, 1, tzinfo=UTC)
    remote_a = datetime(2026, 1, 20, tzinfo=UTC)

    service = _service_with_playback_mocks()
    run_id = uuid4()
    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    set_user_cursor = _mock_user_cursors(
        service, {jf_user_a: None, jf_user_b: cursor_b}
    )
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[_event(connector_user_id=jf_user_a, remote_at=remote_a)],
                users_seen=1,
                user_max_remote_at={jf_user_a: remote_a},
                users_missing=1,
            ),
        ),
    ):
        _enabled_config(
            cfg,
            user_id=user_a,
            user_map={jf_user_a: str(user_a), jf_user_b: str(user_b)},
        )
        await service.poll_once()

    set_user_cursor.assert_called_once_with("jellyfin", jf_user_a, remote_a)


@pytest.mark.anyio
async def test_poll_once_fetch_failure_does_not_advance_failed_user() -> None:
    user_a = uuid4()
    user_b = uuid4()
    jf_user_a = "jf-user-a"
    jf_user_b = "jf-user-b"
    remote_b = datetime(2026, 2, 10, tzinfo=UTC)

    service = _service_with_playback_mocks()
    run_id = uuid4()
    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    set_user_cursor = _mock_user_cursors(service, {jf_user_a: None, jf_user_b: None})
    service.repository.purge_stale_rows = AsyncMock()

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(
            service,
            "_fetch_jellyfin_events",
            return_value=_FetchResult(
                events=[_event(connector_user_id=jf_user_b, remote_at=remote_b)],
                users_seen=1,
                user_max_remote_at={jf_user_b: remote_b},
            ),
        ),
    ):
        _enabled_config(
            cfg,
            user_id=user_a,
            user_map={jf_user_a: str(user_a), jf_user_b: str(user_b)},
        )
        await service.poll_once()

    set_user_cursor.assert_called_once_with("jellyfin", jf_user_b, remote_b)


@pytest.mark.anyio
async def test_poll_once_copied_cursor_avoids_replay_for_existing_user() -> None:
    user_id = uuid4()
    jf_user = "jf-user"
    copied_cursor = datetime(2026, 3, 15, tzinfo=UTC)

    service = _service_with_playback_mocks()
    run_id = uuid4()
    service.repository.start_run = AsyncMock(return_value=MagicMock(id=run_id))
    service.repository.finish_run = AsyncMock()
    get_user_cursors = AsyncMock(
        side_effect=lambda _connector, _user_ids: {jf_user: copied_cursor}
    )
    service.repository.get_user_cursors = get_user_cursors
    service.repository.set_user_cursor = AsyncMock()
    service.repository.purge_stale_rows = AsyncMock()

    fetch_mock = MagicMock(
        return_value=_FetchResult(events=[], users_seen=1, user_max_remote_at={})
    )

    with (
        patch("miramedia.viewing_sync.service.MiraMediaConfig") as cfg,
        patch.object(
            service.repository,
            "load_media_catalog",
            new=AsyncMock(return_value=_empty_catalog()),
        ),
        patch.object(service, "_fetch_jellyfin_events", fetch_mock),
    ):
        _enabled_config(cfg, user_id=user_id, jellyfin_user=jf_user)
        await service.poll_once()

    assert fetch_mock.call_args.args[-1][jf_user] == copied_cursor


def test_fetch_jellyfin_events_uses_per_user_cursors() -> None:
    user_a = "user-a"
    user_b = "user-b"
    cursor_a = datetime(2026, 1, 1, tzinfo=UTC)
    cursor_b = datetime(2026, 2, 1, tzinfo=UTC)
    seen_filters: dict[str, datetime | None] = {}

    class _FakeClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def list_users(self) -> list[MagicMock]:
            return [MagicMock(id=user_a), MagicMock(id=user_b)]

        def iter_user_items(
            self,
            user_id: str,
            *,
            min_last_played_date: datetime | None = None,
        ) -> list[dict[str, object]]:
            seen_filters[user_id] = min_last_played_date
            return []

    with patch(
        "miramedia.viewing_sync.service.JellyfinClient",
        return_value=_FakeClient(),
    ):
        result = ViewingSyncDryRunService._fetch_jellyfin_events(
            "http://127.0.0.1:8096",
            "key",
            30,
            True,
            True,
            {user_a: uuid4(), user_b: uuid4()},
            {user_a: cursor_a, user_b: cursor_b},
        )

    assert seen_filters == {user_a: cursor_a, user_b: cursor_b}
    assert result.users_seen == 2
    assert result.users_missing == 0


def test_fetch_jellyfin_events_missing_user_retains_no_advance_entry() -> None:
    user_a = "user-a"
    user_b = "user-b"

    class _FakeClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def list_users(self) -> list[MagicMock]:
            return [MagicMock(id=user_a)]

        def iter_user_items(
            self,
            _user_id: str,
            *,
            min_last_played_date: datetime | None = None,
        ) -> list[dict[str, object]]:
            _ = min_last_played_date
            return []

    with (
        patch(
            "miramedia.viewing_sync.service.JellyfinClient",
            return_value=_FakeClient(),
        ),
        patch("miramedia.viewing_sync.service.viewing_metric_inc") as metric_inc,
    ):
        result = ViewingSyncDryRunService._fetch_jellyfin_events(
            "http://127.0.0.1:8096",
            "key",
            30,
            True,
            True,
            {user_a: uuid4(), user_b: uuid4()},
            {user_a: datetime(2026, 1, 1, tzinfo=UTC), user_b: None},
        )

    assert user_b not in result.user_max_remote_at
    assert result.users_missing == 1
    metric_inc.assert_any_call("viewing_sync_users_missing")


def test_fetch_jellyfin_events_user_failure_does_not_advance() -> None:
    user_a = "user-a"
    user_b = "user-b"
    remote_b = datetime(2026, 2, 5, tzinfo=UTC)

    class _FakeClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def list_users(self) -> list[MagicMock]:
            return [MagicMock(id=user_a), MagicMock(id=user_b)]

        def iter_user_items(
            self,
            user_id: str,
            *,
            min_last_played_date: datetime | None = None,
        ) -> list[dict[str, object]]:
            _ = min_last_played_date
            if user_id == user_a:
                msg = "upstream failed"
                raise JellyfinError(msg)
            return [
                {
                    "Id": "item-b",
                    "Type": "Movie",
                    "Name": "Example",
                    "RunTimeTicks": 100_000 * 10_000,
                    "ProviderIds": {"Imdb": "tt123"},
                    "UserData": {
                        "PlaybackPositionTicks": 0,
                        "Played": True,
                        "PlayCount": 1,
                        "LastPlayedDate": remote_b.isoformat().replace("+00:00", "Z"),
                    },
                }
            ]

    with patch(
        "miramedia.viewing_sync.service.JellyfinClient",
        return_value=_FakeClient(),
    ):
        result = ViewingSyncDryRunService._fetch_jellyfin_events(
            "http://127.0.0.1:8096",
            "key",
            30,
            True,
            True,
            {user_a: uuid4(), user_b: uuid4()},
            {user_a: None, user_b: None},
        )

    assert user_a not in result.user_max_remote_at
    assert result.user_max_remote_at[user_b] == remote_b

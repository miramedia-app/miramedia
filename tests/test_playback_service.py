"""Service-level tests for playback progress upsert policy."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from miramedia.playback.repository import PlaybackRepository
from miramedia.playback.schemas import (
    MediaKind,
    PlaybackProgress,
    PlaybackProgressUpsert,
    SeasonWatchStateUpdate,
    ShowWatchStateUpdate,
    WatchStateUpdate,
)
from miramedia.playback.service import PlaybackService
from tests.fakes.repositories import FakePlaybackRepository

pytestmark = pytest.mark.anyio


def _service(
    *,
    repository: AsyncMock | None = None,
    movie_repository: AsyncMock | None = None,
    show_repository: AsyncMock | None = None,
) -> tuple[PlaybackService, AsyncMock, AsyncMock, AsyncMock]:
    repo = repository or AsyncMock()
    movie_repo = movie_repository or AsyncMock()
    show_repo = show_repository or AsyncMock()
    return PlaybackService(repo, movie_repo, show_repo), repo, movie_repo, show_repo


def _progress(
    *,
    file_id: uuid.UUID,
    position_ms: int,
    duration_ms: int = 100_000,
    completed: bool = False,
    updated_at: datetime | None = None,
    media_kind: MediaKind = MediaKind.movie,
) -> PlaybackProgress:
    return PlaybackProgress(
        file_id=file_id,
        media_kind=media_kind,
        position_ms=position_ms,
        duration_ms=duration_ms,
        completed=completed,
        updated_at=updated_at or datetime.now(UTC),
    )


def test_upsert_marks_completed_near_end() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = None
    repo.upsert_progress.return_value = _progress(
        file_id=file_id, position_ms=95_000, completed=True
    )

    result = asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=95_000,
                duration_ms=100_000,
            ),
        )
    )

    assert result is not None
    assert result.completed is True
    repo.upsert_progress.assert_awaited_once()
    kwargs = repo.upsert_progress.await_args.kwargs
    assert kwargs["completed"] is True


def test_upsert_seek_back_clears_completed() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = _progress(
        file_id=file_id, position_ms=95_000, completed=True
    )
    repo.upsert_progress.return_value = _progress(
        file_id=file_id, position_ms=10_000, completed=False
    )

    result = asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=10_000,
                duration_ms=100_000,
            ),
        )
    )

    assert result is not None
    assert result.completed is False
    kwargs = repo.upsert_progress.await_args.kwargs
    assert kwargs["completed"] is False


def test_upsert_below_noise_floor_without_row_is_noop() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = None

    result = asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=1_000,
                duration_ms=100_000,
            ),
        )
    )

    assert result is None
    repo.upsert_progress.assert_not_awaited()


def test_upsert_completion_transition_bypasses_coalescing() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing = _progress(
        file_id=file_id,
        position_ms=89_500,
        completed=False,
        updated_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = existing
    repo.upsert_progress.return_value = _progress(
        file_id=file_id, position_ms=90_500, completed=True
    )

    asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=90_500,
                duration_ms=100_000,
            ),
        )
    )

    repo.upsert_progress.assert_awaited_once()
    assert repo.upsert_progress.await_args.kwargs["completed"] is True


def test_upsert_uncompletion_transition_bypasses_coalescing() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing = _progress(
        file_id=file_id,
        position_ms=90_500,
        completed=True,
        updated_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = existing
    repo.upsert_progress.return_value = _progress(
        file_id=file_id, position_ms=89_600, completed=False
    )

    asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=89_600,
                duration_ms=100_000,
            ),
        )
    )

    repo.upsert_progress.assert_awaited_once()
    assert repo.upsert_progress.await_args.kwargs["completed"] is False


def test_upsert_still_coalesces_when_completed_unchanged() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing = _progress(
        file_id=file_id,
        position_ms=60_000,
        completed=False,
        updated_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = existing

    result = asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=61_000,
                duration_ms=100_000,
            ),
        )
    )

    assert result is existing
    repo.upsert_progress.assert_not_awaited()


def test_upsert_coalesces_small_recent_writes() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing = _progress(
        file_id=file_id,
        position_ms=60_000,
        updated_at=datetime.now(UTC) - timedelta(seconds=2),
    )
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = existing

    result = asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=61_000,
                duration_ms=100_000,
            ),
        )
    )

    assert result is existing
    repo.upsert_progress.assert_not_awaited()


def test_upsert_last_write_wins_after_coalesce_window() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing = _progress(
        file_id=file_id,
        position_ms=60_000,
        updated_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    updated = _progress(file_id=file_id, position_ms=80_000, completed=False)
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = existing
    repo.upsert_progress.return_value = updated

    result = asyncio.run(
        service.upsert_progress(
            user_id=user_id,
            data=PlaybackProgressUpsert(
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=80_000,
                duration_ms=100_000,
            ),
        )
    )

    assert result == updated
    repo.upsert_progress.assert_awaited_once()


def test_get_progress_owner_scoped_lookup() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    progress = _progress(file_id=file_id, position_ms=50_000)
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_file_by_id.return_value = MagicMock()
    repo.get_progress.return_value = progress

    result = asyncio.run(
        service.get_progress(
            user_id=user_id,
            file_id=file_id,
            media_kind=MediaKind.movie,
        )
    )

    assert result == progress
    repo.get_progress.assert_awaited_once_with(
        user_id=user_id,
        file_id=file_id,
        media_kind=MediaKind.movie,
    )


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.commit = AsyncMock()
    return db


class TestPlaybackRepositoryUpsert:
    def test_movie_upsert_uses_on_conflict_with_partial_predicate(self) -> None:
        db = _mock_db()
        repo = PlaybackRepository(db)  # type: ignore[arg-type]
        user_id = uuid.uuid4()
        file_id = uuid.uuid4()
        row = MagicMock()
        row.movie_file_id = file_id
        row.episode_file_id = None
        row.position_ms = 60_000
        row.duration_ms = 100_000
        row.completed = False
        row.updated_at = datetime.now(UTC)
        db.execute.return_value.scalars.return_value.one.return_value = row

        asyncio.run(
            repo.upsert_progress(
                user_id=user_id,
                file_id=file_id,
                media_kind=MediaKind.movie,
                position_ms=60_000,
                duration_ms=100_000,
                completed=False,
            )
        )

        assert db.execute.await_count >= 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT" in sql
        assert "movie_file_id IS NOT NULL" in sql

    def test_episode_upsert_uses_on_conflict_with_partial_predicate(self) -> None:
        db = _mock_db()
        repo = PlaybackRepository(db)  # type: ignore[arg-type]
        user_id = uuid.uuid4()
        file_id = uuid.uuid4()
        row = MagicMock()
        row.movie_file_id = None
        row.episode_file_id = file_id
        row.position_ms = 60_000
        row.duration_ms = 100_000
        row.completed = False
        row.updated_at = datetime.now(UTC)
        db.execute.return_value.scalars.return_value.one.return_value = row

        asyncio.run(
            repo.upsert_progress(
                user_id=user_id,
                file_id=file_id,
                media_kind=MediaKind.episode,
                position_ms=60_000,
                duration_ms=100_000,
                completed=False,
            )
        )

        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT" in sql
        assert "episode_file_id IS NOT NULL" in sql


class TestPlaybackRepositoryContinue:
    def test_list_continue_uses_joined_statement_with_limit(self) -> None:
        db = _mock_db()
        repo = PlaybackRepository(db)  # type: ignore[arg-type]
        db.execute.return_value.all.return_value = []

        asyncio.run(repo.list_continue(user_id=uuid.uuid4(), limit=20))

        assert db.execute.await_count >= 1
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "LEFT OUTER JOIN movie_file" in sql
        assert "LEFT OUTER JOIN episode_file" in sql
        assert "LIMIT" in sql
        assert "ORDER BY playback_progress.updated_at DESC" in sql
        assert sql.index("JOIN") < sql.index("LIMIT")


def test_delete_progress_missing_row_is_idempotent() -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    service, repo, _, _ = _service()

    asyncio.run(service.delete_progress(user_id=user_id, file_id=file_id))

    repo.delete_progress.assert_awaited_once_with(user_id=user_id, file_id=file_id)


def _watched_service(
    playback_repo: FakePlaybackRepository | None = None,
) -> tuple[PlaybackService, FakePlaybackRepository, AsyncMock, AsyncMock]:
    repo = playback_repo or FakePlaybackRepository()
    movie_repo = AsyncMock()
    show_repo = AsyncMock()
    movie_repo.get_movie_by_id = AsyncMock(return_value=MagicMock())
    show_repo.get_episode = AsyncMock(return_value=MagicMock())
    return PlaybackService(repo, movie_repo, show_repo), repo, movie_repo, show_repo


async def test_manual_unwatched_wins_over_completed_progress() -> None:
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    movie_id = uuid.uuid4()
    repo = FakePlaybackRepository()
    repo.seed_logical_media(
        file_id=file_id, media_kind=MediaKind.movie, media_id=movie_id
    )
    service, repo, _, _ = _watched_service(repo)
    await repo.upsert_progress(
        user_id=user_id,
        file_id=file_id,
        media_kind=MediaKind.movie,
        position_ms=95_000,
        duration_ms=100_000,
        completed=True,
    )
    await service.set_watched(
        user_id=user_id,
        data=WatchStateUpdate(
            media_kind="movie",
            media_id=movie_id,
            watched=False,
        ),
    )
    state = await service.get_watched(
        user_id=user_id,
        media_kind=MediaKind.movie,
        media_id=movie_id,
    )
    assert state.watched is False
    assert state.source == "manual"


async def test_completion_creates_derived_watched_state() -> None:
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    movie_id = uuid.uuid4()
    repo = FakePlaybackRepository()
    repo.seed_logical_media(
        file_id=file_id, media_kind=MediaKind.movie, media_id=movie_id
    )
    service, _, movie_repo, _ = _watched_service(repo)
    movie_repo.get_movie_file_by_id = AsyncMock(return_value=MagicMock())
    await service.upsert_progress(
        user_id=user_id,
        data=PlaybackProgressUpsert(
            file_id=file_id,
            media_kind=MediaKind.movie,
            position_ms=95_000,
            duration_ms=100_000,
        ),
    )
    state = await service.get_watched(
        user_id=user_id,
        media_kind=MediaKind.movie,
        media_id=movie_id,
    )
    assert state.watched is True
    assert state.source == "derived"


async def test_seek_back_clears_only_derived_state_without_completed_siblings() -> None:
    user_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    file_a = uuid.uuid4()
    file_b = uuid.uuid4()
    repo = FakePlaybackRepository()
    repo.seed_logical_media(
        file_id=file_a, media_kind=MediaKind.episode, media_id=episode_id
    )
    repo.seed_logical_media(
        file_id=file_b, media_kind=MediaKind.episode, media_id=episode_id
    )
    service, _, _movie_repo, show_repo = _watched_service(repo)
    show_repo.get_episode_file_by_id = AsyncMock(return_value=MagicMock())
    await repo.upsert_progress(
        user_id=user_id,
        file_id=file_a,
        media_kind=MediaKind.episode,
        position_ms=95_000,
        duration_ms=100_000,
        completed=True,
    )
    await repo.upsert_progress(
        user_id=user_id,
        file_id=file_b,
        media_kind=MediaKind.episode,
        position_ms=95_000,
        duration_ms=100_000,
        completed=True,
    )
    await service.upsert_progress(
        user_id=user_id,
        data=PlaybackProgressUpsert(
            file_id=file_a,
            media_kind=MediaKind.episode,
            position_ms=10_000,
            duration_ms=100_000,
        ),
    )
    state = await service.get_watched(
        user_id=user_id,
        media_kind=MediaKind.episode,
        media_id=episode_id,
    )
    assert state.watched is True

    file_only = uuid.uuid4()
    solo_episode = uuid.uuid4()
    solo_repo = FakePlaybackRepository()
    solo_repo.seed_logical_media(
        file_id=file_only, media_kind=MediaKind.episode, media_id=solo_episode
    )
    solo_service, _, _, solo_show_repo = _watched_service(solo_repo)
    solo_show_repo.get_episode_file_by_id = AsyncMock(return_value=MagicMock())
    await solo_repo.upsert_progress(
        user_id=user_id,
        file_id=file_only,
        media_kind=MediaKind.episode,
        position_ms=95_000,
        duration_ms=100_000,
        completed=True,
    )
    await solo_service.upsert_progress(
        user_id=user_id,
        data=PlaybackProgressUpsert(
            file_id=file_only,
            media_kind=MediaKind.episode,
            position_ms=10_000,
            duration_ms=100_000,
        ),
    )
    solo_state = await solo_service.get_watched(
        user_id=user_id,
        media_kind=MediaKind.episode,
        media_id=solo_episode,
    )
    assert solo_state.watched is False
    assert solo_state.source is None


async def test_manual_watched_survives_file_deletion() -> None:
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    movie_id = uuid.uuid4()
    repo = FakePlaybackRepository()
    repo.seed_logical_media(
        file_id=file_id, media_kind=MediaKind.movie, media_id=movie_id
    )
    service, _, _, _ = _watched_service(repo)
    await service.set_watched(
        user_id=user_id,
        data=WatchStateUpdate(
            media_kind="movie",
            media_id=movie_id,
            watched=True,
        ),
    )
    await repo.upsert_progress(
        user_id=user_id,
        file_id=file_id,
        media_kind=MediaKind.movie,
        position_ms=60_000,
        duration_ms=100_000,
        completed=False,
    )
    await service.delete_progress(user_id=user_id, file_id=file_id)
    state = await service.get_watched(
        user_id=user_id,
        media_kind=MediaKind.movie,
        media_id=movie_id,
    )
    assert state.watched is True
    assert state.source == "manual"


async def test_get_watched_isolated_by_user() -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    movie_id = uuid.uuid4()
    repo = FakePlaybackRepository()
    service, _, _, _ = _watched_service(repo)
    await service.set_watched(
        user_id=owner_id,
        data=WatchStateUpdate(
            media_kind="movie",
            media_id=movie_id,
            watched=True,
        ),
    )
    owner_state = await service.get_watched(
        user_id=owner_id,
        media_kind=MediaKind.movie,
        media_id=movie_id,
    )
    other_state = await service.get_watched(
        user_id=other_id,
        media_kind=MediaKind.movie,
        media_id=movie_id,
    )
    assert owner_state.watched is True
    assert other_state.watched is False


async def test_mark_show_excludes_specials_by_default() -> None:
    from miramedia.shows.schemas import Episode as EpisodeSchema
    from miramedia.shows.schemas import (
        EpisodeId,
        EpisodeNumber,
        Season,
        SeasonNumber,
        ShowId,
    )

    user_id = uuid.uuid4()
    show_id = ShowId(uuid.uuid4())
    special_episode_id = EpisodeId(uuid.uuid4())
    regular_episode_id = EpisodeId(uuid.uuid4())
    repo = FakePlaybackRepository()
    service, repo, _, show_repo = _watched_service(repo)
    show_repo.get_show_by_id = AsyncMock(
        return_value=MagicMock(
            seasons=[
                Season(
                    number=SeasonNumber(0),
                    episodes=[
                        EpisodeSchema(
                            id=special_episode_id,
                            number=EpisodeNumber(1),
                            title="Special",
                        )
                    ],
                ),
                Season(
                    number=SeasonNumber(1),
                    episodes=[
                        EpisodeSchema(
                            id=regular_episode_id,
                            number=EpisodeNumber(1),
                            title="Pilot",
                        )
                    ],
                ),
            ]
        )
    )
    await service.set_show_watched(
        user_id=user_id,
        data=ShowWatchStateUpdate(show_id=UUID(str(show_id)), watched=True),
    )
    special_key = repo._watch_key(
        user_id, MediaKind.episode, UUID(str(special_episode_id))
    )
    regular_key = repo._watch_key(
        user_id, MediaKind.episode, UUID(str(regular_episode_id))
    )
    assert special_key not in repo.watch_states
    assert regular_key in repo.watch_states


async def test_mark_season_batches_all_regular_episodes() -> None:
    from miramedia.shows.schemas import Episode as EpisodeSchema
    from miramedia.shows.schemas import (
        EpisodeId,
        EpisodeNumber,
        Season,
        SeasonNumber,
        ShowId,
    )

    user_id = uuid.uuid4()
    show_id = ShowId(uuid.uuid4())
    ep_one = EpisodeId(uuid.uuid4())
    ep_two = EpisodeId(uuid.uuid4())
    repo = FakePlaybackRepository()
    service, repo, _, show_repo = _watched_service(repo)
    show_repo.get_season_by_number = AsyncMock(
        return_value=Season(
            number=SeasonNumber(1),
            episodes=[
                EpisodeSchema(id=ep_one, number=EpisodeNumber(1), title="One"),
                EpisodeSchema(id=ep_two, number=EpisodeNumber(2), title="Two"),
            ],
        )
    )
    await service.set_season_watched(
        user_id=user_id,
        data=SeasonWatchStateUpdate(
            show_id=UUID(str(show_id)),
            season_number=1,
            watched=True,
        ),
    )
    for episode_id in (ep_one, ep_two):
        key = repo._watch_key(user_id, MediaKind.episode, UUID(str(episode_id)))
        assert repo.watch_states[key].watched is True
        assert repo.watch_states[key].source == "manual"

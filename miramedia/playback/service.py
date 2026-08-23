from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from miramedia.exceptions import NotFoundError
from miramedia.movies.repository import MovieRepository
from miramedia.movies.schemas import MovieId
from miramedia.playback.completion import below_noise_floor, is_completed
from miramedia.playback.repository import PlaybackRepository
from miramedia.playback.schemas import (
    ContinueWatchingItem,
    MediaKind,
    PlaybackProgress,
    PlaybackProgressUpsert,
    SeasonWatchStateUpdate,
    ShowWatchStateUpdate,
    UpNextItem,
    WatchState,
    WatchStateUpdate,
)
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import EpisodeId, ShowId
from miramedia.watchlists.auto_remove import (
    auto_remove_watched_from_lists,
    auto_remove_watched_media_ids,
)

log = logging.getLogger(__name__)

_COALESCE_WINDOW = timedelta(seconds=5)
_COALESCE_POSITION_DELTA_MS = 2_000


class PlaybackService:
    def __init__(
        self,
        repository: PlaybackRepository,
        movie_repository: MovieRepository,
        show_repository: ShowRepository,
    ) -> None:
        self.repository = repository
        self.movie_repository = movie_repository
        self.show_repository = show_repository

    async def _resolve_file(self, file_id: UUID, media_kind: MediaKind) -> None:
        if media_kind == MediaKind.movie:
            row = await self.movie_repository.get_movie_file_by_id(file_id)
            if row is None:
                msg = "Movie file not found"
                raise NotFoundError(msg)
            return
        row = await self.show_repository.get_episode_file_by_id(file_id)
        if row is None:
            msg = "Episode file not found"
            raise NotFoundError(msg)

    async def get_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
        media_kind: MediaKind | None = None,
    ) -> PlaybackProgress | None:
        if media_kind is not None:
            await self._resolve_file(file_id, media_kind)
        progress = await self.repository.get_progress(
            user_id=user_id,
            file_id=file_id,
            media_kind=media_kind,
        )
        if progress is None and media_kind is None:
            return None
        if progress is not None and media_kind is not None:
            if progress.media_kind != media_kind:
                msg = "Playback progress not found"
                raise NotFoundError(msg)
        return progress

    def _should_coalesce_write(
        self,
        *,
        existing: PlaybackProgress | None,
        position_ms: int,
        completed: bool,
        now: datetime,
    ) -> bool:
        if existing is None:
            return False
        if completed != existing.completed:
            return False
        delta = now - existing.updated_at
        if delta >= _COALESCE_WINDOW:
            return False
        return abs(position_ms - existing.position_ms) < _COALESCE_POSITION_DELTA_MS

    async def upsert_progress(
        self,
        *,
        user_id: UUID,
        data: PlaybackProgressUpsert,
    ) -> PlaybackProgress | None:
        await self._resolve_file(data.file_id, data.media_kind)
        completed = is_completed(data.position_ms, data.duration_ms)
        if below_noise_floor(data.position_ms, completed=completed):
            return await self.repository.get_progress(
                user_id=user_id,
                file_id=data.file_id,
                media_kind=data.media_kind,
            )

        existing = await self.repository.get_progress(
            user_id=user_id,
            file_id=data.file_id,
            media_kind=data.media_kind,
        )
        now = datetime.now(UTC)
        if self._should_coalesce_write(
            existing=existing,
            position_ms=data.position_ms,
            completed=completed,
            now=now,
        ):
            return existing

        became_completed = completed and (existing is None or not existing.completed)
        progress = await self.repository.upsert_progress(
            user_id=user_id,
            file_id=data.file_id,
            media_kind=data.media_kind,
            position_ms=data.position_ms,
            duration_ms=data.duration_ms,
            completed=completed,
        )
        if became_completed:
            media_id = await self.repository.get_logical_media_id(
                file_id=data.file_id,
                media_kind=data.media_kind,
            )
            await auto_remove_watched_from_lists(
                self.repository.db,
                user_id=user_id,
                media_kind="movie" if data.media_kind == MediaKind.movie else "episode",
                media_id=media_id,
            )
        return progress

    async def delete_progress(self, *, user_id: UUID, file_id: UUID) -> None:
        await self.repository.delete_progress(user_id=user_id, file_id=file_id)

    async def delete_all_progress(self, *, user_id: UUID) -> None:
        await self.repository.delete_all_progress(user_id=user_id)

    async def list_continue(
        self,
        *,
        user_id: UUID,
        limit: int,
    ) -> list[ContinueWatchingItem]:
        return await self.repository.list_continue(user_id=user_id, limit=limit)

    async def list_up_next(
        self,
        *,
        user_id: UUID,
        limit: int,
        include_specials: bool = False,
    ) -> list[UpNextItem]:
        return await self.repository.list_up_next(
            user_id=user_id,
            limit=limit,
            include_specials=include_specials,
        )

    async def _resolve_logical_media(
        self, media_kind: MediaKind, media_id: UUID
    ) -> None:
        if media_kind == MediaKind.movie:
            try:
                await self.movie_repository.get_movie_by_id(MovieId(media_id))
            except NotFoundError:
                msg = "Movie not found"
                raise NotFoundError(msg) from None
            return
        try:
            await self.show_repository.get_episode(EpisodeId(media_id))
        except NotFoundError:
            msg = "Episode not found"
            raise NotFoundError(msg) from None

    async def get_watched(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> WatchState:
        await self._resolve_logical_media(media_kind, media_id)
        return await self.repository.get_watched(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )

    async def set_watched(
        self,
        *,
        user_id: UUID,
        data: WatchStateUpdate,
    ) -> WatchState:
        media_kind = MediaKind(data.media_kind)
        await self._resolve_logical_media(media_kind, data.media_id)
        state = await self.repository.set_watched(
            user_id=user_id,
            media_kind=media_kind,
            media_id=data.media_id,
            watched=data.watched,
        )
        if data.watched:
            await auto_remove_watched_from_lists(
                self.repository.db,
                user_id=user_id,
                media_kind="movie" if media_kind == MediaKind.movie else "episode",
                media_id=data.media_id,
            )
        return state

    async def clear_watched_override(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> WatchState:
        await self._resolve_logical_media(media_kind, media_id)
        return await self.repository.clear_watched_override(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )

    async def set_season_watched(
        self,
        *,
        user_id: UUID,
        data: SeasonWatchStateUpdate,
    ) -> None:
        try:
            season = await self.show_repository.get_season_by_number(
                data.season_number, ShowId(data.show_id)
            )
        except NotFoundError:
            msg = "Season not found"
            raise NotFoundError(msg) from None
        if data.season_number == 0 and not data.include_specials:
            return
        episode_ids = [UUID(str(episode.id)) for episode in season.episodes]
        await self.repository.set_episodes_watched(
            user_id=user_id,
            episode_ids=episode_ids,
            watched=data.watched,
        )
        if data.watched:
            await auto_remove_watched_media_ids(
                self.repository.db,
                user_id=user_id,
                media_kind="episode",
                media_ids=episode_ids,
            )

    async def set_show_watched(
        self,
        *,
        user_id: UUID,
        data: ShowWatchStateUpdate,
    ) -> None:
        try:
            show = await self.show_repository.get_show_by_id(ShowId(data.show_id))
        except NotFoundError:
            msg = "Show not found"
            raise NotFoundError(msg) from None
        episode_ids: list[UUID] = []
        for season in show.seasons:
            if season.number == 0 and not data.include_specials:
                continue
            episode_ids.extend(UUID(str(episode.id)) for episode in season.episodes)
        await self.repository.set_episodes_watched(
            user_id=user_id,
            episode_ids=episode_ids,
            watched=data.watched,
        )
        if data.watched:
            await auto_remove_watched_media_ids(
                self.repository.db,
                user_id=user_id,
                media_kind="episode",
                media_ids=episode_ids,
            )
            await auto_remove_watched_from_lists(
                self.repository.db,
                user_id=user_id,
                media_kind="show",
                media_id=data.show_id,
            )

    async def delete_all_viewing_state(self, *, user_id: UUID) -> None:
        await self.repository.delete_all_viewing_state(user_id=user_id)

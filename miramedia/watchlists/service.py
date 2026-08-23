from __future__ import annotations

from uuid import UUID

from miramedia.config import MiraMediaConfig
from miramedia.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from miramedia.movies.repository import MovieRepository
from miramedia.movies.schemas import MovieId
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import EpisodeId, ShowId
from miramedia.watchlists.repository import WatchlistRepository
from miramedia.watchlists.schemas import (
    WatchlistCreate,
    WatchlistDetail,
    WatchlistItemCreate,
    WatchlistItemView,
    WatchlistMediaKind,
    WatchlistReorder,
    WatchlistSummary,
    WatchlistUpdate,
)

_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 80
_DESCRIPTION_MAX_LEN = 500


class WatchlistService:
    def __init__(
        self,
        repository: WatchlistRepository,
        movie_repository: MovieRepository,
        show_repository: ShowRepository,
    ) -> None:
        self.repository = repository
        self.movie_repository = movie_repository
        self.show_repository = show_repository

    def _normalize_name(self, name: str) -> str:
        trimmed = name.strip()
        if not (_NAME_MIN_LEN <= len(trimmed) <= _NAME_MAX_LEN):
            msg = f"name must be between {_NAME_MIN_LEN} and {_NAME_MAX_LEN} characters"
            raise UnprocessableEntityError(msg)
        return trimmed

    def _normalize_description(self, description: str | None) -> str | None:
        if description is None:
            return None
        trimmed = description.strip()
        if not trimmed:
            return None
        if len(trimmed) > _DESCRIPTION_MAX_LEN:
            msg = f"description must be at most {_DESCRIPTION_MAX_LEN} characters"
            raise UnprocessableEntityError(msg)
        return trimmed

    async def _ensure_unique_name(
        self,
        *,
        user_id: UUID,
        name: str,
        exclude_watchlist_id: UUID | None = None,
    ) -> None:
        if await self.repository.name_taken(
            user_id=user_id,
            name=name,
            exclude_watchlist_id=exclude_watchlist_id,
        ):
            msg = "A watchlist with this name already exists"
            raise ConflictError(msg)

    async def list_watchlists(self, *, user_id: UUID) -> list[WatchlistSummary]:
        return await self.repository.list_summaries(user_id=user_id)

    async def create_watchlist(
        self,
        *,
        user_id: UUID,
        data: WatchlistCreate,
    ) -> WatchlistDetail:
        name = self._normalize_name(data.name)
        description = self._normalize_description(data.description)
        await self._ensure_unique_name(user_id=user_id, name=name)
        max_lists = MiraMediaConfig().watchlists.max_lists_per_user
        if max_lists > 0:
            current = await self.repository.count_lists(user_id=user_id)
            if current >= max_lists:
                msg = f"Maximum of {max_lists} lists per user reached"
                raise UnprocessableEntityError(msg)
        row = await self.repository.create(
            user_id=user_id,
            name=name,
            description=description,
        )
        detail = await self.repository.get_detail(
            user_id=user_id,
            watchlist_id=row.id,
        )
        if detail is None:
            msg = "watchlist row missing after create"
            raise RuntimeError(msg)
        return detail

    async def get_watchlist(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
    ) -> WatchlistDetail | None:
        return await self.repository.get_detail(
            user_id=user_id,
            watchlist_id=watchlist_id,
        )

    async def update_watchlist(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        data: WatchlistUpdate,
    ) -> WatchlistDetail | None:
        name: str | None = None
        description: str | None | object = ...
        if data.name is not None:
            name = self._normalize_name(data.name)
            await self._ensure_unique_name(
                user_id=user_id,
                name=name,
                exclude_watchlist_id=watchlist_id,
            )
        if data.description is not None:
            description = self._normalize_description(data.description)
        row = await self.repository.update(
            user_id=user_id,
            watchlist_id=watchlist_id,
            name=name,
            description=description,
        )
        if row is None:
            return None
        return await self.repository.get_detail(
            user_id=user_id,
            watchlist_id=watchlist_id,
        )

    async def delete_watchlist(self, *, user_id: UUID, watchlist_id: UUID) -> bool:
        return await self.repository.delete(user_id=user_id, watchlist_id=watchlist_id)

    async def _resolve_media_exists(
        self,
        *,
        media_kind: WatchlistMediaKind,
        media_id: UUID,
    ) -> None:
        try:
            if media_kind == "movie":
                await self.movie_repository.get_movie_by_id(MovieId(media_id))
                return
            if media_kind == "show":
                await self.show_repository.get_show_by_id(ShowId(media_id))
                return
            await self.show_repository.get_episode(EpisodeId(media_id))
        except NotFoundError as exc:
            msg = f"{media_kind.title()} not found"
            raise NotFoundError(msg) from exc

    async def add_item(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        data: WatchlistItemCreate,
    ) -> tuple[WatchlistItemView, bool]:
        await self._resolve_media_exists(
            media_kind=data.media_kind,
            media_id=data.media_id,
        )
        result = await self.repository.add_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            media_kind=data.media_kind,
            media_id=data.media_id,
        )
        if result is None:
            msg = "Watchlist not found"
            raise NotFoundError(msg)
        return result

    async def remove_watched_media_from_lists(
        self,
        *,
        user_id: UUID,
        media_kind: WatchlistMediaKind,
        media_id: UUID,
    ) -> int:
        if not MiraMediaConfig().watchlists.auto_remove_watched:
            return 0
        if not MiraMediaConfig().watchlists.custom_lists_enabled:
            return 0
        return await self.repository.delete_items_for_media(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )

    async def reorder_items(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        data: WatchlistReorder,
    ) -> WatchlistDetail:
        if len(set(data.item_ids)) != len(data.item_ids):
            msg = "item_ids must be an exact permutation of the current watchlist items"
            raise UnprocessableEntityError(msg)
        detail = await self.repository.reorder_items(
            user_id=user_id,
            watchlist_id=watchlist_id,
            item_ids=data.item_ids,
        )
        if detail is None:
            owned = await self.repository.get_owned(
                user_id=user_id,
                watchlist_id=watchlist_id,
            )
            if owned is None:
                msg = "Watchlist not found"
                raise NotFoundError(msg)
            msg = "item_ids must be an exact permutation of the current watchlist items"
            raise UnprocessableEntityError(msg)
        return detail

    async def remove_item(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        item_id: UUID,
    ) -> bool:
        return await self.repository.remove_item(
            user_id=user_id,
            watchlist_id=watchlist_id,
            item_id=item_id,
        )

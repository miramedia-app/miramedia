from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.exceptions import ConflictError, UnprocessableEntityError
from miramedia.movies.models import Movie, MovieFile
from miramedia.naming import format_episode_label
from miramedia.playback.models import MediaWatchState as MediaWatchStateRow
from miramedia.playback.models import PlaybackProgress as PlaybackProgressRow
from miramedia.playback.models import WatchStateSource
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.watchlists.models import Watchlist, WatchlistItem
from miramedia.watchlists.schemas import (
    WatchlistDetail,
    WatchlistItemView,
    WatchlistMediaKind,
    WatchlistNextEpisode,
    WatchlistSummary,
)


@dataclass(frozen=True, slots=True)
class _FilePick:
    file_id: UUID
    position_ms: int
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class _ShowNextEpisodePick:
    show_id: UUID
    media_id: UUID
    episode_number: int
    episode_title: str | None
    season_number: int
    file_id: UUID
    position_ms: int
    duration_ms: int | None
    watched: bool


class WatchlistRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_summaries(self, *, user_id: UUID) -> list[WatchlistSummary]:
        item_count = func.count(WatchlistItem.id)
        stmt = (
            select(Watchlist, item_count.label("item_count"))
            .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == user_id)
            .group_by(Watchlist.id)
            .order_by(Watchlist.name.asc())
        )
        rows = (await self.db.execute(stmt)).all()
        covers = await self._cover_posters_for_watchlists(
            [row.Watchlist.id for row in rows]
        )
        return [
            WatchlistSummary(
                id=row.Watchlist.id,
                name=row.Watchlist.name,
                description=row.Watchlist.description,
                item_count=int(row.item_count),
                cover_poster_media_id=covers.get(row.Watchlist.id),
                created_at=row.Watchlist.created_at,
                updated_at=row.Watchlist.updated_at,
            )
            for row in rows
        ]

    async def count_lists(self, *, user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Watchlist)
            .where(Watchlist.user_id == user_id)
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def count_items(self, *, watchlist_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def delete_items_for_media(
        self,
        *,
        user_id: UUID,
        media_kind: WatchlistMediaKind,
        media_id: UUID,
    ) -> int:
        """Remove matching items from every list owned by ``user_id``."""
        if media_kind == "movie":
            media_col = WatchlistItem.movie_id
        elif media_kind == "show":
            media_col = WatchlistItem.show_id
        else:
            media_col = WatchlistItem.episode_id

        list_ids = select(Watchlist.id).where(Watchlist.user_id == user_id)
        result = await self.db.execute(
            delete(WatchlistItem).where(
                WatchlistItem.watchlist_id.in_(list_ids),
                media_col == media_id,
            )
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    async def delete_items_for_media_ids(
        self,
        *,
        user_id: UUID,
        media_kind: WatchlistMediaKind,
        media_ids: list[UUID],
    ) -> int:
        """Batch variant of :meth:`delete_items_for_media`.

        One DELETE for the whole id set, scoped to lists owned by
        ``user_id``; single commit, so the removal is atomic.
        """
        if not media_ids:
            return 0
        if media_kind == "movie":
            media_col = WatchlistItem.movie_id
        elif media_kind == "show":
            media_col = WatchlistItem.show_id
        else:
            media_col = WatchlistItem.episode_id

        list_ids = select(Watchlist.id).where(Watchlist.user_id == user_id)
        result = await self.db.execute(
            delete(WatchlistItem).where(
                WatchlistItem.watchlist_id.in_(list_ids),
                media_col.in_(media_ids),
            )
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    async def _cover_posters_for_watchlists(
        self,
        watchlist_ids: list[UUID],
    ) -> dict[UUID, UUID]:
        """Poster media id of the lowest-position item per watchlist."""
        if not watchlist_ids:
            return {}
        ranked = (
            select(
                WatchlistItem.watchlist_id,
                WatchlistItem.movie_id,
                WatchlistItem.show_id,
                WatchlistItem.episode_id,
                func.row_number()
                .over(
                    partition_by=WatchlistItem.watchlist_id,
                    order_by=(
                        WatchlistItem.position.asc(),
                        WatchlistItem.created_at.asc(),
                    ),
                )
                .label("rn"),
            )
            .where(WatchlistItem.watchlist_id.in_(watchlist_ids))
            .subquery()
        )
        stmt = (
            select(
                ranked.c.watchlist_id,
                ranked.c.movie_id,
                ranked.c.show_id,
                Season.show_id.label("episode_show_id"),
            )
            .outerjoin(Episode, Episode.id == ranked.c.episode_id)
            .outerjoin(Season, Season.id == Episode.season_id)
            .where(ranked.c.rn == 1)
        )
        covers: dict[UUID, UUID] = {}
        for row in (await self.db.execute(stmt)).all():
            if row.movie_id is not None:
                covers[row.watchlist_id] = row.movie_id
            elif row.show_id is not None:
                covers[row.watchlist_id] = row.show_id
            elif row.episode_show_id is not None:
                covers[row.watchlist_id] = row.episode_show_id
        return covers

    async def name_taken(
        self,
        *,
        user_id: UUID,
        name: str,
        exclude_watchlist_id: UUID | None = None,
    ) -> bool:
        stmt = select(Watchlist.id).where(
            Watchlist.user_id == user_id,
            func.lower(Watchlist.name) == name.lower(),
        )
        if exclude_watchlist_id is not None:
            stmt = stmt.where(Watchlist.id != exclude_watchlist_id)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def create(
        self,
        *,
        user_id: UUID,
        name: str,
        description: str | None,
    ) -> Watchlist:
        row = Watchlist(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name,
            description=description,
        )
        self.db.add(row)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            msg = "A watchlist with this name already exists"
            raise ConflictError(msg) from exc
        await self.db.refresh(row)
        return row

    async def get_owned(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
    ) -> Watchlist | None:
        stmt = select(Watchlist).where(
            Watchlist.id == watchlist_id,
            Watchlist.user_id == user_id,
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def get_detail(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
    ) -> WatchlistDetail | None:
        watchlist = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if watchlist is None:
            return None
        stmt = (
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.position.asc(), WatchlistItem.created_at.asc())
        )
        items = (await self.db.execute(stmt)).scalars().all()
        resolved = await self._resolve_items(user_id=user_id, items=items)
        return WatchlistDetail(
            id=watchlist.id,
            name=watchlist.name,
            description=watchlist.description,
            items=resolved,
            created_at=watchlist.created_at,
            updated_at=watchlist.updated_at,
        )

    async def update(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        name: str | None,
        description: str | None | object = ...,
    ) -> Watchlist | None:
        watchlist = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if watchlist is None:
            return None
        if name is not None:
            watchlist.name = name
        if description is not ...:
            watchlist.description = cast(str | None, description)
        watchlist.updated_at = datetime.now(UTC)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            msg = "A watchlist with this name already exists"
            raise ConflictError(msg) from exc
        await self.db.refresh(watchlist)
        return watchlist

    async def delete(self, *, user_id: UUID, watchlist_id: UUID) -> bool:
        watchlist = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if watchlist is None:
            return False
        await self.db.delete(watchlist)
        await self.db.commit()
        return True

    async def _lock_watchlist(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
    ) -> Watchlist | None:
        stmt = (
            select(Watchlist)
            .where(
                Watchlist.id == watchlist_id,
                Watchlist.user_id == user_id,
            )
            .with_for_update()
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def add_item(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        media_kind: WatchlistMediaKind,
        media_id: UUID,
    ) -> tuple[WatchlistItemView, bool] | None:
        watchlist = await self._lock_watchlist(
            user_id=user_id, watchlist_id=watchlist_id
        )
        if watchlist is None:
            return None

        existing = await self._find_existing_item(
            watchlist_id=watchlist_id,
            media_kind=media_kind,
            media_id=media_id,
        )
        if existing is not None:
            resolved = await self._resolve_items(user_id=user_id, items=[existing])
            return resolved[0], False

        max_items = MiraMediaConfig().watchlists.max_items_per_list
        if max_items > 0:
            count_stmt = (
                select(func.count())
                .select_from(WatchlistItem)
                .where(WatchlistItem.watchlist_id == watchlist_id)
            )
            current = int((await self.db.execute(count_stmt)).scalar_one())
            if current >= max_items:
                msg = f"Maximum of {max_items} items per list reached"
                raise UnprocessableEntityError(msg)

        position_stmt = select(
            func.coalesce(func.max(WatchlistItem.position), -1)
        ).where(WatchlistItem.watchlist_id == watchlist_id)
        max_position = (await self.db.execute(position_stmt)).scalar_one()
        row = WatchlistItem(
            id=uuid.uuid4(),
            watchlist_id=watchlist_id,
            position=int(max_position) + 1,
            movie_id=media_id if media_kind == "movie" else None,
            show_id=media_id if media_kind == "show" else None,
            episode_id=media_id if media_kind == "episode" else None,
        )
        self.db.add(row)
        watchlist.updated_at = datetime.now(UTC)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self._find_existing_item(
                watchlist_id=watchlist_id,
                media_kind=media_kind,
                media_id=media_id,
            )
            if existing is None:
                raise
            resolved = await self._resolve_items(user_id=user_id, items=[existing])
            return resolved[0], False
        await self.db.refresh(row)
        resolved = await self._resolve_items(user_id=user_id, items=[row])
        return resolved[0], True

    async def _find_existing_item(
        self,
        *,
        watchlist_id: UUID,
        media_kind: WatchlistMediaKind,
        media_id: UUID,
    ) -> WatchlistItem | None:
        if media_kind == "movie":
            predicate = WatchlistItem.movie_id == media_id
        elif media_kind == "show":
            predicate = WatchlistItem.show_id == media_id
        else:
            predicate = WatchlistItem.episode_id == media_id
        stmt = select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist_id,
            predicate,
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def reorder_items(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        item_ids: list[UUID],
    ) -> WatchlistDetail | None:
        watchlist = await self._lock_watchlist(
            user_id=user_id, watchlist_id=watchlist_id
        )
        if watchlist is None:
            return None
        # Permutation check must run on post-lock state; a pre-lock check
        # would race concurrent add/remove. Payload size is bounded at the
        # schema layer (WatchlistReorder.item_ids max_length).
        stmt = select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist_id)
        current_items = (await self.db.execute(stmt)).scalars().all()
        current_ids = {item.id for item in current_items}
        if set(item_ids) != current_ids or len(item_ids) != len(current_ids):
            return None
        # Temp positions must clear ALL current positions: deletes leave gaps, so
        # max(position) can exceed len(items) - 1 and len() as offset collides with
        # the non-deferrable unique index uq_watchlist_item_list_position.
        offset = max((item.position for item in current_items), default=0) + 1
        for position, item_id in enumerate(item_ids):
            await self.db.execute(
                update(WatchlistItem)
                .where(WatchlistItem.id == item_id)
                .values(position=position + offset)
            )
        for position, item_id in enumerate(item_ids):
            await self.db.execute(
                update(WatchlistItem)
                .where(WatchlistItem.id == item_id)
                .values(position=position)
            )
        watchlist.updated_at = datetime.now(UTC)
        await self.db.commit()
        return await self.get_detail(user_id=user_id, watchlist_id=watchlist_id)

    async def remove_item(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        item_id: UUID,
    ) -> bool:
        watchlist = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if watchlist is None:
            return False
        stmt = delete(WatchlistItem).where(
            WatchlistItem.id == item_id,
            WatchlistItem.watchlist_id == watchlist_id,
        )
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            # The DELETE matched nothing but still opened/extended the
            # transaction; roll back so the session is clean for callers
            # that don't raise (get_session commits on clean return).
            await self.db.rollback()
            return False
        watchlist.updated_at = datetime.now(UTC)
        await self.db.commit()
        return True

    async def _resolve_items(
        self,
        *,
        user_id: UUID,
        items: Sequence[WatchlistItem],
    ) -> list[WatchlistItemView]:
        movie_items = [i for i in items if i.movie_id is not None]
        episode_items = [
            i for i in items if i.movie_id is None and i.episode_id is not None
        ]
        show_items = [
            i
            for i in items
            if i.movie_id is None and i.episode_id is None and i.show_id is not None
        ]
        views: dict[UUID, WatchlistItemView] = {}
        views.update(
            await self._resolve_movie_items(user_id=user_id, items=movie_items)
        )
        views.update(
            await self._resolve_episode_items(user_id=user_id, items=episode_items)
        )
        views.update(await self._resolve_show_items(user_id=user_id, items=show_items))
        return [views[item.id] for item in items if item.id in views]

    async def _resolve_movie_items(
        self,
        *,
        user_id: UUID,
        items: list[WatchlistItem],
    ) -> dict[UUID, WatchlistItemView]:
        if not items:
            return {}
        movie_ids: list[UUID] = []
        for item in items:
            if item.movie_id is None:
                msg = "movie row missing during watchlist resolution"
                raise RuntimeError(msg)
            movie_ids.append(item.movie_id)
        movies = {
            movie.id: movie
            for movie in (
                await self.db.execute(select(Movie).where(Movie.id.in_(movie_ids)))
            )
            .scalars()
            .all()
        }
        file_picks = await self._pick_movie_files_batch(
            user_id=user_id, movie_ids=movie_ids
        )
        watched_map = await self._watched_movies_batch(
            user_id=user_id, movie_ids=movie_ids
        )
        views: dict[UUID, WatchlistItemView] = {}
        for item in items:
            movie_id = item.movie_id
            if movie_id is None:
                msg = "movie row missing during watchlist resolution"
                raise RuntimeError(msg)
            movie = movies.get(movie_id)
            if movie is None:
                msg = "movie row missing during watchlist resolution"
                raise RuntimeError(msg)
            file_row = file_picks.get(movie_id)
            views[item.id] = WatchlistItemView(
                id=item.id,
                position=item.position,
                media_kind="movie",
                media_id=movie_id,
                title=movie.name,
                poster_media_id=movie_id,
                watched=watched_map[movie_id],
                year=movie.year,
                file_id=file_row.file_id if file_row else None,
                position_ms=file_row.position_ms if file_row else None,
                duration_ms=file_row.duration_ms if file_row else None,
            )
        return views

    async def _resolve_episode_items(
        self,
        *,
        user_id: UUID,
        items: list[WatchlistItem],
    ) -> dict[UUID, WatchlistItemView]:
        if not items:
            return {}
        episode_ids: list[UUID] = []
        for item in items:
            if item.episode_id is None:
                raise NoResultFound
            episode_ids.append(item.episode_id)
        stmt = (
            select(Episode, Season, Show)
            .join(Season, Episode.season_id == Season.id)
            .join(Show, Season.show_id == Show.id)
            .where(Episode.id.in_(episode_ids))
        )
        episode_rows: dict[UUID, tuple[Episode, Season, Show]] = {}
        for episode, season, show in (await self.db.execute(stmt)).all():
            episode_rows[episode.id] = (episode, season, show)
        file_picks = await self._pick_episode_files_batch(
            user_id=user_id, episode_ids=episode_ids
        )
        watched_map = await self._watched_episodes_batch(
            user_id=user_id, episode_ids=episode_ids
        )
        views: dict[UUID, WatchlistItemView] = {}
        for item in items:
            episode_id = item.episode_id
            if episode_id is None:
                raise NoResultFound
            row = episode_rows.get(episode_id)
            if row is None:
                raise NoResultFound
            episode, season, show = row
            file_row = file_picks.get(episode_id)
            title = format_episode_label(
                show.name,
                season.number,
                episode.number,
                episode.title,
            )
            views[item.id] = WatchlistItemView(
                id=item.id,
                position=item.position,
                media_kind="episode",
                media_id=episode_id,
                title=title,
                poster_media_id=show.id,
                watched=watched_map[episode_id],
                show_id=show.id,
                season_number=season.number,
                episode_number=episode.number,
                file_id=file_row.file_id if file_row else None,
                position_ms=file_row.position_ms if file_row else None,
                duration_ms=file_row.duration_ms if file_row else None,
            )
        return views

    async def _resolve_show_items(
        self,
        *,
        user_id: UUID,
        items: list[WatchlistItem],
    ) -> dict[UUID, WatchlistItemView]:
        if not items:
            return {}
        show_ids: list[UUID] = []
        for item in items:
            if item.show_id is None:
                msg = "show row missing during watchlist resolution"
                raise RuntimeError(msg)
            show_ids.append(item.show_id)
        shows = {
            show.id: show
            for show in (
                await self.db.execute(select(Show).where(Show.id.in_(show_ids)))
            )
            .scalars()
            .all()
        }
        next_episodes = await self._show_next_episodes_batch(
            user_id=user_id, show_ids=show_ids
        )
        has_imported = await self._shows_with_imported_batch(show_ids=show_ids)
        views: dict[UUID, WatchlistItemView] = {}
        for item in items:
            show_id = item.show_id
            if show_id is None:
                msg = "show row missing during watchlist resolution"
                raise RuntimeError(msg)
            show = shows.get(show_id)
            if show is None:
                msg = "show row missing during watchlist resolution"
                raise RuntimeError(msg)
            next_row = next_episodes.get(show_id)
            if next_row is not None:
                title = format_episode_label(
                    show.name,
                    next_row.season_number,
                    next_row.episode_number,
                    next_row.episode_title,
                )
                next_episode = WatchlistNextEpisode(
                    file_id=next_row.file_id,
                    media_id=next_row.media_id,
                    season_number=next_row.season_number,
                    episode_number=next_row.episode_number,
                    episode_title=next_row.episode_title,
                    title=title,
                    watched=bool(next_row.watched),
                    position_ms=next_row.position_ms,
                    duration_ms=next_row.duration_ms,
                )
                show_status = None
            elif show_id not in has_imported:
                next_episode = None
                show_status = "no_downloaded_episode_available"
            else:
                next_episode = None
                show_status = "all_available_episodes_watched"
            views[item.id] = WatchlistItemView(
                id=item.id,
                position=item.position,
                media_kind="show",
                media_id=show_id,
                title=show.name,
                poster_media_id=show_id,
                watched=False,
                next_episode=next_episode,
                show_status=show_status,
            )
        return views

    _PICK_MOVIE_FILES_BATCH_SQL = text("""
        SELECT DISTINCT ON (mf.movie_id)
            mf.movie_id,
            mf.id AS file_id,
            COALESCE(pp.position_ms, 0) AS position_ms,
            pp.duration_ms
        FROM movie_file mf
        LEFT JOIN playback_progress pp
            ON pp.movie_file_id = mf.id AND pp.user_id = :user_id
        WHERE mf.movie_id = ANY(:movie_ids)
          AND mf.import_status = 'imported'
        ORDER BY
            mf.movie_id,
            CASE
                WHEN pp.id IS NOT NULL AND NOT pp.completed THEN 0
                ELSE 1
            END,
            CASE
                WHEN pp.id IS NOT NULL AND NOT pp.completed THEN pp.updated_at
            END DESC NULLS LAST,
            mf.imported_at DESC NULLS LAST,
            mf.id
    """)

    _PICK_EPISODE_FILES_BATCH_SQL = text("""
        SELECT DISTINCT ON (ef.episode_id)
            ef.episode_id,
            ef.id AS file_id,
            COALESCE(pp.position_ms, 0) AS position_ms,
            pp.duration_ms
        FROM episode_file ef
        LEFT JOIN playback_progress pp
            ON pp.episode_file_id = ef.id AND pp.user_id = :user_id
        WHERE ef.episode_id = ANY(:episode_ids)
          AND ef.import_status = 'imported'
        ORDER BY
            ef.episode_id,
            CASE
                WHEN pp.id IS NOT NULL AND NOT pp.completed THEN 0
                ELSE 1
            END,
            CASE
                WHEN pp.id IS NOT NULL AND NOT pp.completed THEN pp.updated_at
            END DESC NULLS LAST,
            ef.imported_at DESC NULLS LAST,
            ef.id
    """)

    async def _pick_movie_files_batch(
        self,
        *,
        user_id: UUID,
        movie_ids: list[UUID],
    ) -> dict[UUID, _FilePick]:
        rows = (
            await self.db.execute(
                self._PICK_MOVIE_FILES_BATCH_SQL,
                {"user_id": user_id, "movie_ids": movie_ids},
            )
        ).all()
        return {
            row.movie_id: _FilePick(
                file_id=row.file_id,
                position_ms=row.position_ms,
                duration_ms=row.duration_ms,
            )
            for row in rows
        }

    async def _pick_episode_files_batch(
        self,
        *,
        user_id: UUID,
        episode_ids: list[UUID],
    ) -> dict[UUID, _FilePick]:
        rows = (
            await self.db.execute(
                self._PICK_EPISODE_FILES_BATCH_SQL,
                {"user_id": user_id, "episode_ids": episode_ids},
            )
        ).all()
        return {
            row.episode_id: _FilePick(
                file_id=row.file_id,
                position_ms=row.position_ms,
                duration_ms=row.duration_ms,
            )
            for row in rows
        }

    async def _watched_movies_batch(
        self,
        *,
        user_id: UUID,
        movie_ids: list[UUID],
    ) -> dict[UUID, bool]:
        manual_stmt = select(
            MediaWatchStateRow.movie_id,
            MediaWatchStateRow.watched,
        ).where(
            MediaWatchStateRow.user_id == user_id,
            MediaWatchStateRow.movie_id.in_(movie_ids),
            MediaWatchStateRow.source == WatchStateSource.manual,
        )
        manual = {
            row.movie_id: row.watched
            for row in (await self.db.execute(manual_stmt)).all()
        }
        completed_stmt = (
            select(MovieFile.movie_id)
            .join(
                PlaybackProgressRow,
                PlaybackProgressRow.movie_file_id == MovieFile.id,
            )
            .where(
                PlaybackProgressRow.user_id == user_id,
                MovieFile.movie_id.in_(movie_ids),
                PlaybackProgressRow.completed.is_(True),
            )
            .distinct()
        )
        completed = set((await self.db.execute(completed_stmt)).scalars().all())
        return {
            movie_id: (
                manual[movie_id] if movie_id in manual else movie_id in completed
            )
            for movie_id in movie_ids
        }

    async def _watched_episodes_batch(
        self,
        *,
        user_id: UUID,
        episode_ids: list[UUID],
    ) -> dict[UUID, bool]:
        manual_stmt = select(
            MediaWatchStateRow.episode_id,
            MediaWatchStateRow.watched,
        ).where(
            MediaWatchStateRow.user_id == user_id,
            MediaWatchStateRow.episode_id.in_(episode_ids),
            MediaWatchStateRow.source == WatchStateSource.manual,
        )
        manual = {
            row.episode_id: row.watched
            for row in (await self.db.execute(manual_stmt)).all()
        }
        completed_stmt = (
            select(EpisodeFile.episode_id)
            .join(
                PlaybackProgressRow,
                PlaybackProgressRow.episode_file_id == EpisodeFile.id,
            )
            .where(
                PlaybackProgressRow.user_id == user_id,
                EpisodeFile.episode_id.in_(episode_ids),
                PlaybackProgressRow.completed.is_(True),
            )
            .distinct()
        )
        completed = set((await self.db.execute(completed_stmt)).scalars().all())
        return {
            episode_id: (
                manual[episode_id] if episode_id in manual else episode_id in completed
            )
            for episode_id in episode_ids
        }

    _SHOW_NEXT_EPISODES_BATCH_SQL = text("""
        WITH episode_watched AS (
            SELECT
                s.show_id,
                e.id AS episode_id,
                CASE
                    WHEN mws.source = 'manual' THEN mws.watched
                    WHEN EXISTS (
                        SELECT 1
                        FROM playback_progress pp2
                        JOIN episode_file ef2 ON pp2.episode_file_id = ef2.id
                        WHERE pp2.user_id = :user_id
                          AND ef2.episode_id = e.id
                          AND pp2.completed
                    ) THEN true
                    ELSE false
                END AS watched
            FROM episode e
            JOIN season s ON s.id = e.season_id
            LEFT JOIN media_watch_state mws
                ON mws.episode_id = e.id AND mws.user_id = :user_id
            WHERE s.show_id = ANY(:show_ids)
        ),
        ranked_files AS (
            SELECT DISTINCT ON (ef.episode_id)
                ef.id AS file_id,
                ef.episode_id,
                pp.position_ms,
                pp.duration_ms
            FROM episode_file ef
            JOIN episode e ON e.id = ef.episode_id
            JOIN season s ON s.id = e.season_id
            LEFT JOIN playback_progress pp
                ON pp.episode_file_id = ef.id AND pp.user_id = :user_id
            WHERE s.show_id = ANY(:show_ids)
              AND ef.import_status = 'imported'
            ORDER BY
                ef.episode_id,
                CASE
                    WHEN pp.id IS NOT NULL AND NOT pp.completed THEN 0
                    ELSE 1
                END,
                CASE
                    WHEN pp.id IS NOT NULL AND NOT pp.completed THEN pp.updated_at
                END DESC NULLS LAST,
                ef.imported_at DESC NULLS LAST,
                ef.id
        ),
        candidates AS (
            SELECT
                s.show_id,
                e.id AS media_id,
                e.number AS episode_number,
                e.title AS episode_title,
                s.number AS season_number,
                rf.file_id,
                COALESCE(rf.position_ms, 0) AS position_ms,
                rf.duration_ms,
                ew.watched
            FROM season s
            JOIN episode e ON e.season_id = s.id
            JOIN episode_watched ew ON ew.episode_id = e.id
            JOIN ranked_files rf ON rf.episode_id = e.id
            WHERE s.show_id = ANY(:show_ids)
              AND NOT ew.watched
              AND NOT e.skipped
              AND s.number <> 0
        )
        SELECT DISTINCT ON (show_id) *
        FROM candidates
        ORDER BY show_id, season_number, episode_number
    """)

    _SHOW_HAS_IMPORTED_BATCH_SQL = text("""
        SELECT DISTINCT s.show_id
        FROM episode e
        JOIN season s ON s.id = e.season_id
        JOIN episode_file ef ON ef.episode_id = e.id
        WHERE s.show_id = ANY(:show_ids)
          AND s.number <> 0
          AND NOT e.skipped
          AND ef.import_status = 'imported'
    """)

    async def _show_next_episodes_batch(
        self,
        *,
        user_id: UUID,
        show_ids: list[UUID],
    ) -> dict[UUID, _ShowNextEpisodePick]:
        rows = (
            await self.db.execute(
                self._SHOW_NEXT_EPISODES_BATCH_SQL,
                {"user_id": user_id, "show_ids": show_ids},
            )
        ).all()
        return {
            row.show_id: _ShowNextEpisodePick(
                show_id=row.show_id,
                media_id=row.media_id,
                episode_number=row.episode_number,
                episode_title=row.episode_title,
                season_number=row.season_number,
                file_id=row.file_id,
                position_ms=row.position_ms,
                duration_ms=row.duration_ms,
                watched=row.watched,
            )
            for row in rows
        }

    async def _shows_with_imported_batch(
        self,
        *,
        show_ids: list[UUID],
    ) -> set[UUID]:
        rows = (
            await self.db.execute(
                self._SHOW_HAS_IMPORTED_BATCH_SQL,
                {"show_ids": show_ids},
            )
        ).all()
        return {row.show_id for row in rows}

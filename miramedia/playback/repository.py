from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import ColumnElement, delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.movies.models import Movie, MovieFile
from miramedia.movies.schemas import MovieId
from miramedia.naming import format_episode_label
from miramedia.playback.models import MediaWatchState as MediaWatchStateRow
from miramedia.playback.models import PlaybackProgress as PlaybackProgressRow
from miramedia.playback.models import WatchStateSource
from miramedia.playback.schemas import (
    ContinueWatchingItem,
    MediaKind,
    PlaybackProgress,
    UpNextItem,
    WatchState,
    continue_episode_item,
    continue_movie_item,
    new_progress_id,
)
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.schemas import EpisodeId, ShowId

log = logging.getLogger(__name__)


class PlaybackRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _row_file_id(row: PlaybackProgressRow) -> UUID:
        if row.movie_file_id is not None:
            return row.movie_file_id
        if row.episode_file_id is None:
            msg = "playback progress row missing file foreign key"
            raise RuntimeError(msg)
        return row.episode_file_id

    @staticmethod
    def _row_media_kind(row: PlaybackProgressRow) -> MediaKind:
        return MediaKind.movie if row.movie_file_id is not None else MediaKind.episode

    def _to_schema(self, row: PlaybackProgressRow) -> PlaybackProgress:
        return PlaybackProgress(
            file_id=self._row_file_id(row),
            media_kind=self._row_media_kind(row),
            position_ms=row.position_ms,
            duration_ms=row.duration_ms,
            completed=row.completed,
            updated_at=row.updated_at,
        )

    def _file_filter(
        self, file_id: UUID, media_kind: MediaKind | None
    ) -> ColumnElement[bool]:
        if media_kind == MediaKind.movie:
            return PlaybackProgressRow.movie_file_id == file_id
        if media_kind == MediaKind.episode:
            return PlaybackProgressRow.episode_file_id == file_id
        return or_(
            PlaybackProgressRow.movie_file_id == file_id,
            PlaybackProgressRow.episode_file_id == file_id,
        )

    async def get_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
        media_kind: MediaKind | None = None,
    ) -> PlaybackProgress | None:
        stmt = select(PlaybackProgressRow).where(
            PlaybackProgressRow.user_id == user_id,
            self._file_filter(file_id, media_kind),
        )
        row = (await self.db.execute(stmt)).scalars().first()
        return self._to_schema(row) if row else None

    async def upsert_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
        media_kind: MediaKind,
        position_ms: int,
        duration_ms: int,
        completed: bool,
    ) -> PlaybackProgress:
        now = datetime.now(UTC)
        values = {
            "id": new_progress_id(),
            "user_id": user_id,
            "movie_file_id": file_id if media_kind == MediaKind.movie else None,
            "episode_file_id": file_id if media_kind == MediaKind.episode else None,
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "completed": completed,
            "updated_at": now,
        }
        update_set = {
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "completed": completed,
            "updated_at": now,
        }
        if media_kind == MediaKind.movie:
            stmt = (
                pg_insert(PlaybackProgressRow)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        PlaybackProgressRow.user_id,
                        PlaybackProgressRow.movie_file_id,
                    ],
                    index_where=text("movie_file_id IS NOT NULL"),
                    set_=update_set,
                )
                .returning(PlaybackProgressRow)
            )
        else:
            stmt = (
                pg_insert(PlaybackProgressRow)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        PlaybackProgressRow.user_id,
                        PlaybackProgressRow.episode_file_id,
                    ],
                    index_where=text("episode_file_id IS NOT NULL"),
                    set_=update_set,
                )
                .returning(PlaybackProgressRow)
            )
        row = (await self.db.execute(stmt)).scalars().one()
        await self._sync_derived_watch_state(
            user_id=user_id,
            media_kind=media_kind,
            media_id=await self.get_logical_media_id(
                file_id=file_id, media_kind=media_kind
            ),
        )
        await self.db.commit()
        return self._to_schema(row)

    async def delete_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
    ) -> None:
        progress = await self.get_progress(user_id=user_id, file_id=file_id)
        await self.db.execute(
            delete(PlaybackProgressRow).where(
                PlaybackProgressRow.user_id == user_id,
                self._file_filter(file_id, None),
            )
        )
        if progress is not None:
            await self._sync_derived_watch_state(
                user_id=user_id,
                media_kind=progress.media_kind,
                media_id=await self.get_logical_media_id(
                    file_id=file_id, media_kind=progress.media_kind
                ),
            )
        await self.db.commit()

    async def delete_all_progress(self, *, user_id: UUID) -> None:
        await self.db.execute(
            delete(PlaybackProgressRow).where(PlaybackProgressRow.user_id == user_id)
        )
        await self.db.commit()

    async def delete_all_viewing_state(self, *, user_id: UUID) -> None:
        await self.db.execute(
            delete(PlaybackProgressRow).where(PlaybackProgressRow.user_id == user_id)
        )
        await self.db.execute(
            delete(MediaWatchStateRow).where(MediaWatchStateRow.user_id == user_id)
        )
        await self.db.commit()

    async def list_continue(
        self,
        *,
        user_id: UUID,
        limit: int,
    ) -> list[ContinueWatchingItem]:
        stmt = (
            select(
                PlaybackProgressRow,
                Movie.id,
                Movie.name,
                Movie.year,
                Show.id,
                Show.name,
                Season.number,
                Episode.id,
                Episode.number,
            )
            .outerjoin(MovieFile, PlaybackProgressRow.movie_file_id == MovieFile.id)
            .outerjoin(Movie, MovieFile.movie_id == Movie.id)
            .outerjoin(
                EpisodeFile, PlaybackProgressRow.episode_file_id == EpisodeFile.id
            )
            .outerjoin(Episode, EpisodeFile.episode_id == Episode.id)
            .outerjoin(Season, Episode.season_id == Season.id)
            .outerjoin(Show, Season.show_id == Show.id)
            .where(
                PlaybackProgressRow.user_id == user_id,
                PlaybackProgressRow.completed.is_(False),
                or_(Movie.id.is_not(None), Show.id.is_not(None)),
            )
            .order_by(PlaybackProgressRow.updated_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        items: list[ContinueWatchingItem] = []
        for row in rows:
            (
                row_obj,
                movie_id,
                movie_name,
                movie_year,
                show_id,
                show_name,
                season_number,
                episode_id,
                episode_number,
            ) = row
            if row_obj.movie_file_id is not None:
                items.append(
                    continue_movie_item(
                        file_id=row_obj.movie_file_id,
                        movie_id=MovieId(movie_id),
                        title=movie_name,
                        year=movie_year,
                        position_ms=row_obj.position_ms,
                        duration_ms=row_obj.duration_ms,
                        updated_at=row_obj.updated_at,
                    )
                )
            else:
                items.append(
                    continue_episode_item(
                        file_id=row_obj.episode_file_id,
                        episode_id=EpisodeId(episode_id),
                        show_id=ShowId(show_id),
                        title=show_name,
                        season_number=season_number,
                        episode_number=episode_number,
                        position_ms=row_obj.position_ms,
                        duration_ms=row_obj.duration_ms,
                        updated_at=row_obj.updated_at,
                    )
                )
        return items

    async def get_logical_media_id(
        self, *, file_id: UUID, media_kind: MediaKind
    ) -> UUID:
        if media_kind == MediaKind.movie:
            stmt = select(MovieFile.movie_id).where(MovieFile.id == file_id)
            movie_id = (await self.db.execute(stmt)).scalar_one_or_none()
            if movie_id is None:
                msg = "movie file missing logical media id"
                raise RuntimeError(msg)
            return movie_id
        stmt = select(EpisodeFile.episode_id).where(EpisodeFile.id == file_id)
        episode_id = (await self.db.execute(stmt)).scalar_one_or_none()
        if episode_id is None:
            msg = "episode file missing logical media id"
            raise RuntimeError(msg)
        return episode_id

    def _watch_state_to_schema(
        self,
        *,
        media_kind: MediaKind,
        media_id: UUID,
        watched: bool,
        source: WatchStateSource | None,
        watched_at: datetime | None,
    ) -> WatchState:
        schema_source: Literal["derived", "manual"] | None
        if source is None:
            schema_source = None
        elif source is WatchStateSource.manual:
            schema_source = "manual"
        else:
            schema_source = "derived"
        return WatchState(
            media_kind="movie" if media_kind == MediaKind.movie else "episode",
            media_id=media_id,
            watched=watched,
            source=schema_source,
            watched_at=watched_at,
        )

    async def _get_watch_state_row(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> MediaWatchStateRow | None:
        if media_kind == MediaKind.movie:
            stmt = select(MediaWatchStateRow).where(
                MediaWatchStateRow.user_id == user_id,
                MediaWatchStateRow.movie_id == media_id,
            )
        else:
            stmt = select(MediaWatchStateRow).where(
                MediaWatchStateRow.user_id == user_id,
                MediaWatchStateRow.episode_id == media_id,
            )
        return (await self.db.execute(stmt)).scalars().first()

    async def _has_completed_progress(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> bool:
        if media_kind == MediaKind.movie:
            stmt = (
                select(PlaybackProgressRow.id)
                .join(MovieFile, PlaybackProgressRow.movie_file_id == MovieFile.id)
                .where(
                    PlaybackProgressRow.user_id == user_id,
                    MovieFile.movie_id == media_id,
                    PlaybackProgressRow.completed.is_(True),
                )
                .limit(1)
            )
        else:
            stmt = (
                select(PlaybackProgressRow.id)
                .join(
                    EpisodeFile,
                    PlaybackProgressRow.episode_file_id == EpisodeFile.id,
                )
                .where(
                    PlaybackProgressRow.user_id == user_id,
                    EpisodeFile.episode_id == media_id,
                    PlaybackProgressRow.completed.is_(True),
                )
                .limit(1)
            )
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def _sync_derived_watch_state(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> None:
        row = await self._get_watch_state_row(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )
        if row is not None and row.source == WatchStateSource.manual:
            return

        has_completed = await self._has_completed_progress(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )
        now = datetime.now(UTC)
        if has_completed:
            values = {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "movie_id": media_id if media_kind == MediaKind.movie else None,
                "episode_id": media_id if media_kind == MediaKind.episode else None,
                "watched": True,
                "source": WatchStateSource.derived,
                "watched_at": now,
                "updated_at": now,
            }
            update_set = {
                "watched": True,
                "source": WatchStateSource.derived,
                "watched_at": now,
                "updated_at": now,
            }
            if media_kind == MediaKind.movie:
                stmt = (
                    pg_insert(MediaWatchStateRow)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[
                            MediaWatchStateRow.user_id,
                            MediaWatchStateRow.movie_id,
                        ],
                        index_where=text("movie_id IS NOT NULL"),
                        set_=update_set,
                        where=MediaWatchStateRow.source == WatchStateSource.derived,
                    )
                )
            else:
                stmt = (
                    pg_insert(MediaWatchStateRow)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[
                            MediaWatchStateRow.user_id,
                            MediaWatchStateRow.episode_id,
                        ],
                        index_where=text("episode_id IS NOT NULL"),
                        set_=update_set,
                        where=MediaWatchStateRow.source == WatchStateSource.derived,
                    )
                )
            await self.db.execute(stmt)
            return

        if media_kind == MediaKind.movie:
            await self.db.execute(
                delete(MediaWatchStateRow).where(
                    MediaWatchStateRow.user_id == user_id,
                    MediaWatchStateRow.movie_id == media_id,
                    MediaWatchStateRow.source == WatchStateSource.derived,
                )
            )
        else:
            await self.db.execute(
                delete(MediaWatchStateRow).where(
                    MediaWatchStateRow.user_id == user_id,
                    MediaWatchStateRow.episode_id == media_id,
                    MediaWatchStateRow.source == WatchStateSource.derived,
                )
            )

    async def get_watched(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> WatchState:
        row = await self._get_watch_state_row(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )
        if row is not None and row.source == WatchStateSource.manual:
            return self._watch_state_to_schema(
                media_kind=media_kind,
                media_id=media_id,
                watched=row.watched,
                source=WatchStateSource.manual,
                watched_at=row.watched_at,
            )

        has_completed = await self._has_completed_progress(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )
        if has_completed:
            watched_at = row.watched_at if row is not None else None
            if watched_at is None and row is not None:
                watched_at = row.updated_at
            return self._watch_state_to_schema(
                media_kind=media_kind,
                media_id=media_id,
                watched=True,
                source=WatchStateSource.derived,
                watched_at=watched_at,
            )

        return self._watch_state_to_schema(
            media_kind=media_kind,
            media_id=media_id,
            watched=False,
            source=None,
            watched_at=None,
        )

    async def set_watched(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
        watched: bool,
    ) -> WatchState:
        now = datetime.now(UTC)
        values = {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "movie_id": media_id if media_kind == MediaKind.movie else None,
            "episode_id": media_id if media_kind == MediaKind.episode else None,
            "watched": watched,
            "source": WatchStateSource.manual,
            "watched_at": now if watched else None,
            "updated_at": now,
        }
        update_set = {
            "watched": watched,
            "source": WatchStateSource.manual,
            "watched_at": now if watched else None,
            "updated_at": now,
        }
        if media_kind == MediaKind.movie:
            stmt = (
                pg_insert(MediaWatchStateRow)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        MediaWatchStateRow.user_id,
                        MediaWatchStateRow.movie_id,
                    ],
                    index_where=text("movie_id IS NOT NULL"),
                    set_=update_set,
                )
            )
        else:
            stmt = (
                pg_insert(MediaWatchStateRow)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        MediaWatchStateRow.user_id,
                        MediaWatchStateRow.episode_id,
                    ],
                    index_where=text("episode_id IS NOT NULL"),
                    set_=update_set,
                )
            )
        await self.db.execute(stmt)
        await self.db.commit()
        return self._watch_state_to_schema(
            media_kind=media_kind,
            media_id=media_id,
            watched=watched,
            source=WatchStateSource.manual,
            watched_at=now if watched else None,
        )

    async def clear_watched_override(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> WatchState:
        if media_kind == MediaKind.movie:
            await self.db.execute(
                delete(MediaWatchStateRow).where(
                    MediaWatchStateRow.user_id == user_id,
                    MediaWatchStateRow.movie_id == media_id,
                    MediaWatchStateRow.source == WatchStateSource.manual,
                )
            )
        else:
            await self.db.execute(
                delete(MediaWatchStateRow).where(
                    MediaWatchStateRow.user_id == user_id,
                    MediaWatchStateRow.episode_id == media_id,
                    MediaWatchStateRow.source == WatchStateSource.manual,
                )
            )
        await self.db.commit()
        return await self.get_watched(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )

    async def set_episodes_watched(
        self,
        *,
        user_id: UUID,
        episode_ids: list[UUID],
        watched: bool,
    ) -> None:
        if not episode_ids:
            return
        now = datetime.now(UTC)
        values = [
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "movie_id": None,
                "episode_id": episode_id,
                "watched": watched,
                "source": WatchStateSource.manual,
                "watched_at": now if watched else None,
                "updated_at": now,
            }
            for episode_id in episode_ids
        ]
        update_set = {
            "watched": watched,
            "source": WatchStateSource.manual,
            "watched_at": now if watched else None,
            "updated_at": now,
        }
        stmt = (
            pg_insert(MediaWatchStateRow)
            .values(values)
            .on_conflict_do_update(
                index_elements=[
                    MediaWatchStateRow.user_id,
                    MediaWatchStateRow.episode_id,
                ],
                index_where=text("episode_id IS NOT NULL"),
                set_=update_set,
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()

    _UP_NEXT_SQL = text("""
        WITH show_activity AS (
            SELECT
                s.show_id,
                MAX(
                    GREATEST(
                        COALESCE(mws.updated_at, '-infinity'::timestamptz),
                        COALESCE(pp.updated_at, '-infinity'::timestamptz)
                    )
                ) AS activity_at
            FROM season s
            JOIN episode e ON e.season_id = s.id
            LEFT JOIN media_watch_state mws
                ON mws.episode_id = e.id AND mws.user_id = :user_id
            LEFT JOIN episode_file ef ON ef.episode_id = e.id
            LEFT JOIN playback_progress pp
                ON pp.episode_file_id = ef.id AND pp.user_id = :user_id
            WHERE (:include_specials OR s.number <> 0)
            GROUP BY s.show_id
            HAVING
                COUNT(mws.id) FILTER (WHERE mws.id IS NOT NULL) > 0
                OR COUNT(pp.id) FILTER (WHERE pp.id IS NOT NULL) > 0
        ),
        episode_watched AS (
            SELECT
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
            WHERE s.show_id IN (SELECT show_id FROM show_activity)
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
            WHERE ef.import_status = 'imported'
              AND s.show_id IN (SELECT show_id FROM show_activity)
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
                sh.id AS show_id,
                sh.name AS show_name,
                e.id AS media_id,
                e.number AS episode_number,
                e.title AS episode_title,
                s.number AS season_number,
                rf.file_id,
                COALESCE(rf.position_ms, 0) AS position_ms,
                rf.duration_ms,
                sa.activity_at
            FROM show sh
            JOIN show_activity sa ON sa.show_id = sh.id
            JOIN season s ON s.show_id = sh.id
            JOIN episode e ON e.season_id = s.id
            JOIN episode_watched ew ON ew.episode_id = e.id
            JOIN ranked_files rf ON rf.episode_id = e.id
            WHERE NOT ew.watched
              AND NOT e.skipped
              AND (:include_specials OR s.number <> 0)
        ),
        picked AS (
            SELECT DISTINCT ON (show_id)
                show_id,
                show_name,
                media_id,
                episode_number,
                episode_title,
                season_number,
                file_id,
                position_ms,
                duration_ms,
                activity_at
            FROM candidates
            ORDER BY show_id, season_number, episode_number
        )
        SELECT
            file_id,
            media_id,
            show_id,
            show_name,
            season_number,
            episode_number,
            episode_title,
            position_ms,
            duration_ms,
            activity_at
        FROM picked
        ORDER BY activity_at DESC, show_name ASC
        LIMIT :limit
    """)

    async def list_up_next(
        self,
        *,
        user_id: UUID,
        limit: int,
        include_specials: bool = False,
    ) -> list[UpNextItem]:
        rows = (
            await self.db.execute(
                self._UP_NEXT_SQL,
                {
                    "user_id": user_id,
                    "limit": limit,
                    "include_specials": include_specials,
                },
            )
        ).all()
        items: list[UpNextItem] = []
        for row in rows:
            title = format_episode_label(
                row.show_name,
                row.season_number,
                row.episode_number,
                row.episode_title,
            )
            items.append(
                UpNextItem(
                    file_id=row.file_id,
                    media_id=row.media_id,
                    show_id=row.show_id,
                    show_name=row.show_name,
                    season_number=row.season_number,
                    episode_number=row.episode_number,
                    episode_title=row.episode_title,
                    title=title,
                    poster_media_id=row.show_id,
                    position_ms=row.position_ms,
                    duration_ms=row.duration_ms,
                    activity_at=row.activity_at,
                )
            )
        return items

    async def explain_up_next(
        self,
        *,
        user_id: UUID,
        limit: int,
        include_specials: bool = False,
    ) -> list[tuple[str]]:
        explain_sql = text("EXPLAIN (ANALYZE, BUFFERS) " + self._UP_NEXT_SQL.text)
        result = await self.db.execute(
            explain_sql,
            {
                "user_id": user_id,
                "limit": limit,
                "include_specials": include_specials,
            },
        )
        return [(row[0],) for row in result.all()]

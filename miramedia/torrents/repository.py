import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.orm import selectinload

from miramedia.database import DbSessionDependency
from miramedia.exceptions import NotFoundError
from miramedia.movies.models import Movie, MovieFile
from miramedia.movies.schemas import Movie as MovieSchema
from miramedia.movies.schemas import MovieFile as MovieFileSchema
from miramedia.pagination import (
    decode_cursor,
    encode_cursor,
    parse_datetime,
    parse_uuid,
)
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.shows.schemas import Show as ShowSchema
from miramedia.torrents.models import (
    ManualParseToken,
    Torrent,
    TorrentBlock,
    TorrentHistory,
)
from miramedia.torrents.schemas import Torrent as TorrentSchema
from miramedia.torrents.schemas import TorrentHistoryOutcome, TorrentId, TorrentStatus

log = logging.getLogger(__name__)


class TorrentRepository:
    def __init__(self, db: DbSessionDependency) -> None:
        self.db = db

    async def get_episode_files_of_torrent(
        self, torrent_id: TorrentId
    ) -> list[EpisodeFileSchema]:
        stmt = select(EpisodeFile).where(EpisodeFile.torrent_id == torrent_id)
        result = (await self.db.execute(stmt)).scalars().all()
        return [
            EpisodeFileSchema.model_validate(episode_file) for episode_file in result
        ]

    async def get_show_of_torrent(self, torrent_id: TorrentId) -> ShowSchema | None:
        # ``ShowSchema.model_validate`` walks the full show -> seasons ->
        # episodes -> episode_files tree; every relation along that path must
        # be eager-loaded because pydantic's attribute reads happen outside
        # the greenlet-spawn context (lazy-load raises MissingGreenlet).
        stmt = (
            select(Show)
            .options(
                selectinload(Show.seasons)
                .selectinload(Season.episodes)
                .selectinload(Episode.episode_files),
            )
            .join(Show.seasons)
            .join(Season.episodes)
            .join(Episode.episode_files)
            .where(EpisodeFile.torrent_id == torrent_id)
        )
        result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
        if result is None:
            return None
        return ShowSchema.model_validate(result)

    async def save_torrent(self, torrent: TorrentSchema) -> TorrentSchema:
        # Atomic upsert keyed on the unique ``hash`` column. INSERT...ON
        # CONFLICT DO UPDATE avoids the race between a select-then-insert
        # pattern (two concurrent callers with the same hash would both
        # miss the existing row and then collide on insert). Returns the
        # final row's id so callers see the canonical Torrent.
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        payload = torrent.model_dump(
            exclude={"progress", "num_peers", "num_seeds", "download_speed"}
        )
        stmt = (
            pg_insert(Torrent.__table__)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=["hash"],
                set_={
                    "status": payload.get("status"),
                    "title": payload.get("title"),
                    "quality": payload.get("quality"),
                    "usenet": payload.get("usenet"),
                },
            )
            .returning(Torrent.__table__.c.id)
        )
        result_id = (await self.db.execute(stmt)).scalar_one()
        await self.db.commit()
        row = (
            (await self.db.execute(select(Torrent).where(Torrent.id == result_id)))
            .unique()
            .scalar_one()
        )
        return TorrentSchema.model_validate(row)

    async def get_all_torrents(self) -> list[TorrentSchema]:
        stmt = select(Torrent)
        result = (await self.db.execute(stmt)).scalars().all()

        return [
            TorrentSchema.model_validate(torrent_schema) for torrent_schema in result
        ]

    # ---- torrent history (durable download log) -------------------------

    async def record_torrent_downloaded(
        self,
        torrent: TorrentSchema,
        *,
        media_type: str | None = None,
        media_id: uuid.UUID | None = None,
        media_name: str | None = None,
        media_year: int | None = None,
        files_total: int = 0,
    ) -> None:
        """Insert/refresh the ``downloaded`` history row for a grabbed torrent.

        On a re-grab of the same hash, refreshes the descriptive columns but
        leaves the lifecycle (``outcome`` / ``imported_at``) untouched so a
        prior import is not demoted back to ``downloaded``.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        norm = self._normalize_hash(torrent.hash) if torrent.hash else None
        if norm is None:
            return
        stmt = (
            pg_insert(TorrentHistory.__table__)
            .values(
                id=uuid.uuid4(),
                torrent_id=torrent.id,
                info_hash=norm,
                title=torrent.title,
                quality=torrent.quality,
                usenet=torrent.usenet,
                media_type=media_type,
                media_id=media_id,
                media_name=media_name,
                media_year=media_year,
                outcome=TorrentHistoryOutcome.downloaded.value,
                files=[],
                files_total=files_total,
                files_imported=0,
            )
            .on_conflict_do_update(
                index_elements=["info_hash"],
                index_where=text("info_hash IS NOT NULL"),
                set_={
                    "torrent_id": torrent.id,
                    "title": torrent.title,
                    "quality": torrent.quality,
                    "usenet": torrent.usenet,
                    "media_type": media_type,
                    "media_id": media_id,
                    "media_name": media_name,
                    "media_year": media_year,
                    "files_total": files_total,
                },
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def record_torrent_imported(
        self,
        torrent: TorrentSchema,
        *,
        outcome: str,
        files: list,
        files_total: int,
        files_imported: int,
        import_error: str | None,
        imported_at: datetime | None,
        media_type: str | None = None,
        media_id: uuid.UUID | None = None,
        media_name: str | None = None,
        media_year: int | None = None,
    ) -> None:
        """Upsert the history row with the import outcome + file snapshot."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        norm = self._normalize_hash(torrent.hash) if torrent.hash else None
        if norm is None:
            return
        stmt = (
            pg_insert(TorrentHistory.__table__)
            .values(
                id=uuid.uuid4(),
                torrent_id=torrent.id,
                info_hash=norm,
                title=torrent.title,
                quality=torrent.quality,
                usenet=torrent.usenet,
                media_type=media_type,
                media_id=media_id,
                media_name=media_name,
                media_year=media_year,
                outcome=outcome,
                files=files,
                files_total=files_total,
                files_imported=files_imported,
                import_error=import_error,
                imported_at=imported_at,
            )
            .on_conflict_do_update(
                index_elements=["info_hash"],
                index_where=text("info_hash IS NOT NULL"),
                set_={
                    "torrent_id": torrent.id,
                    "title": torrent.title,
                    "quality": torrent.quality,
                    "usenet": torrent.usenet,
                    "media_type": media_type,
                    "media_id": media_id,
                    "media_name": media_name,
                    "media_year": media_year,
                    "outcome": outcome,
                    "files": files,
                    "files_total": files_total,
                    "files_imported": files_imported,
                    "import_error": import_error,
                    "imported_at": imported_at,
                },
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def mark_torrent_history_removed(self, info_hash: str) -> None:
        """Stamp ``removed_at`` when the live torrent is deleted. Keeps the
        prior ``outcome`` (an imported torrent stays ``imported``)."""
        norm = self._normalize_hash(info_hash) if info_hash else None
        if norm is None:
            return
        from sqlalchemy import update

        await self.db.execute(
            update(TorrentHistory)
            .where(TorrentHistory.info_hash == norm)
            .values(removed_at=datetime.now(UTC))
        )
        await self.db.commit()

    async def list_imported_torrent_history(self) -> list[TorrentHistory]:
        """History rows that reached a successful import — powers Imports Done."""
        stmt = (
            select(TorrentHistory)
            .where(TorrentHistory.outcome == TorrentHistoryOutcome.imported.value)
            .order_by(TorrentHistory.imported_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_torrents_paginated(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[TorrentSchema], int, str | None]:
        """Paginated variant of :meth:`get_all_torrents` for list endpoints.

        ``cursor`` enables keyset pagination on ``(created_at DESC, id DESC)``.
        ``offset`` remains supported for the current numbered-page UI.
        """
        count_stmt = select(func.count()).select_from(Torrent)
        stmt = (
            select(Torrent)
            .order_by(Torrent.created_at.desc(), Torrent.id.desc())
            .limit(limit + 1)
        )
        if cursor:
            payload = decode_cursor(cursor)
            created_at = parse_datetime(payload.get("created_at") if payload else None)
            cursor_id = parse_uuid(payload.get("id") if payload else None)
            if created_at is None or cursor_id is None:
                msg = "Invalid torrent pagination cursor"
                raise ValueError(msg)
            stmt = stmt.where(
                or_(
                    Torrent.created_at < created_at,
                    and_(Torrent.created_at == created_at, Torrent.id < cursor_id),
                )
            )
        else:
            stmt = stmt.offset(offset)

        total = (await self.db.scalar(count_stmt)) or 0
        rows = (await self.db.execute(stmt)).scalars().all()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            next_cursor = encode_cursor(
                {"created_at": last.created_at.isoformat(), "id": str(last.id)}
            )
        return (
            [TorrentSchema.model_validate(t) for t in page_rows],
            int(total),
            next_cursor,
        )

    async def get_torrent_by_id(self, torrent_id: TorrentId) -> TorrentSchema:
        result = await self.db.get(Torrent, torrent_id)
        if result is None:
            msg = f"Torrent with ID {torrent_id} not found."
            raise NotFoundError(msg)
        return TorrentSchema.model_validate(result)

    async def delete_torrent(
        self, torrent_id: TorrentId, delete_associated_media_files: bool = False
    ) -> None:
        if delete_associated_media_files:
            # Only drop file rows that never finished importing ("queued" /
            # pending / failed). Already-imported files are real library media
            # (hardlinked to their own inodes) and must survive a torrent
            # delete — the torrent.id FK is ``ON DELETE SET NULL`` so those
            # rows simply lose their torrent link when the torrent row goes.
            from miramedia.file_status import ImportOutcome

            movie_files_stmt = delete(MovieFile).where(
                MovieFile.torrent_id == torrent_id,
                MovieFile.import_status != ImportOutcome.imported,
            )
            await self.db.execute(movie_files_stmt)

            episode_files_stmt = delete(EpisodeFile).where(
                EpisodeFile.torrent_id == torrent_id,
                EpisodeFile.import_status != ImportOutcome.imported,
            )
            await self.db.execute(episode_files_stmt)

        torrent = await self.db.get(Torrent, torrent_id)
        if torrent is not None:
            # Stamp the durable history row (keeps its outcome — an imported
            # torrent stays "imported", just records it left the client).
            if torrent.hash:
                from sqlalchemy import update

                await self.db.execute(
                    update(TorrentHistory)
                    .where(
                        TorrentHistory.info_hash == self._normalize_hash(torrent.hash)
                    )
                    .values(removed_at=datetime.now(UTC))
                )
            await self.db.delete(torrent)

        # Commit like every other mutation here. Without this the row-delete is
        # only *staged*: callers that re-raise after a link failure (see
        # ``download_and_link``'s cleanup branch) trigger a session rollback that
        # undoes the delete, stranding the torrent as an unlinked "ghost" on the
        # torrents page — while its file rows (removed via their own committing
        # helpers) are really gone.
        await self.db.commit()

    async def get_movie_of_torrent(self, torrent_id: TorrentId) -> MovieSchema | None:
        stmt = (
            select(Movie)
            .join(MovieFile, Movie.id == MovieFile.movie_id)
            .where(MovieFile.torrent_id == torrent_id)
        )
        result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
        if result is None:
            return None
        return MovieSchema.model_validate(result)

    async def get_movie_files_of_torrent(
        self, torrent_id: TorrentId
    ) -> list[MovieFileSchema]:
        stmt = select(MovieFile).where(MovieFile.torrent_id == torrent_id)
        result = (await self.db.execute(stmt)).scalars().all()
        return [MovieFileSchema.model_validate(movie_file) for movie_file in result]

    async def get_episode_files_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> list[EpisodeFileSchema]:
        """Batch variant of :meth:`get_episode_files_of_torrent`."""
        if not torrent_ids:
            return []
        stmt = select(EpisodeFile).where(EpisodeFile.torrent_id.in_(torrent_ids))
        result = (await self.db.execute(stmt)).scalars().all()
        return [EpisodeFileSchema.model_validate(ef) for ef in result]

    async def get_movie_files_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> list[MovieFileSchema]:
        """Batch variant of :meth:`get_movie_files_of_torrent`."""
        if not torrent_ids:
            return []
        stmt = select(MovieFile).where(MovieFile.torrent_id.in_(torrent_ids))
        result = (await self.db.execute(stmt)).scalars().all()
        return [MovieFileSchema.model_validate(mf) for mf in result]

    async def get_active_torrent_count(self) -> int:
        """Count torrents that haven't reached the finished state yet."""
        stmt = (
            select(func.count())
            .select_from(Torrent)
            .where(Torrent.status != TorrentStatus.finished)
        )
        return (await self.db.execute(stmt)).scalar_one()

    # Manual parse tokens ----------------------------------------------------

    async def save_manual_parse_token(self, token_id: uuid.UUID, payload: dict) -> None:
        # tz-aware: created_at is TIMESTAMPTZ. A naive value round-trips only on
        # a UTC-session server; keep it aware to match the rest of the codebase.
        self.db.add(
            ManualParseToken(
                id=token_id,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )
        await self.db.commit()

    async def pop_manual_parse_token(self, token_id: uuid.UUID) -> dict | None:
        # Atomic delete-and-fetch: a read-then-delete let two concurrent
        # manual/download calls with the same token both read the row before
        # either committed the delete → double download. DELETE ... RETURNING
        # gives the payload to exactly one caller.
        stmt = (
            delete(ManualParseToken)
            .where(ManualParseToken.id == token_id)
            .returning(ManualParseToken.payload)
        )
        result = await self.db.execute(stmt)
        row = result.first()
        await self.db.commit()
        return row[0] if row else None

    async def delete_expired_manual_parse_tokens(self, ttl_minutes: int = 30) -> int:
        cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)
        stmt = delete(ManualParseToken).where(ManualParseToken.created_at < cutoff)
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount or 0

    # Blocked torrent hashes (deny-list) -------------------------------------

    @staticmethod
    def _normalize_hash(info_hash: str) -> str:
        return info_hash.strip().lower()

    async def is_hash_blocked(self, info_hash: str) -> bool:
        if not info_hash:
            return False
        # Case-insensitive on both sides — historical rows (e.g. manual
        # INSERTs) may be uppercase; live writes go through
        # ``_normalize_hash`` and are lowercase.
        stmt = select(TorrentBlock.info_hash).where(
            func.lower(TorrentBlock.info_hash) == self._normalize_hash(info_hash)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def add_blocked_hash(
        self,
        info_hash: str,
        *,
        title: str | None = None,
        reason: str = "no_video_files",
    ) -> None:
        if not info_hash:
            return
        normalized = self._normalize_hash(info_hash)
        existing = (
            await self.db.execute(
                select(TorrentBlock).where(TorrentBlock.info_hash == normalized)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self.db.add(
            TorrentBlock(
                info_hash=normalized,
                title=title,
                reason=reason,
            )
        )
        await self.db.commit()

    async def list_blocked_hashes(self) -> list[TorrentBlock]:
        stmt = select(TorrentBlock).order_by(TorrentBlock.blocked_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def remove_blocked_hash(self, info_hash: str) -> bool:
        stmt = delete(TorrentBlock).where(
            TorrentBlock.info_hash == self._normalize_hash(info_hash)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return (result.rowcount or 0) > 0

    async def delete_orphaned_torrents(self) -> int:
        """Delete torrents not linked to any episode_file or movie_file."""
        has_episode = exists().where(EpisodeFile.torrent_id == Torrent.id)
        has_movie = exists().where(MovieFile.torrent_id == Torrent.id)
        stmt = select(Torrent.id).where(~has_episode & ~has_movie)
        orphan_ids = (await self.db.execute(stmt)).scalars().all()
        if orphan_ids:
            await self.db.execute(delete(Torrent).where(Torrent.id.in_(orphan_ids)))
            await self.db.commit()
            log.info(f"Deleted {len(orphan_ids)} orphaned torrents")
        return len(orphan_ids)

    async def get_show_contexts_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, dict]:
        """Batch resolve show + season/episode numbers + variant tag per torrent.

        Single query in place of the N+1 the list endpoint used to do.
        Returns a map keyed by torrent_id whose value is a dict with keys:
        ``show_id``, ``show_name``, ``show_year``, ``metadata_provider``,
        ``seasons`` (sorted unique ints), ``episodes`` (sorted unique ints),
        ``variant`` (from the first associated EpisodeFile).
        """
        if not torrent_ids:
            return {}
        stmt = (
            select(
                EpisodeFile.torrent_id,
                Show.id,
                Show.name,
                Show.year,
                Show.metadata_provider,
                Season.number,
                Episode.number,
                EpisodeFile.variant,
            )
            .join(Episode, EpisodeFile.episode_id == Episode.id)
            .join(Season, Episode.season_id == Season.id)
            .join(Show, Season.show_id == Show.id)
            .where(EpisodeFile.torrent_id.in_(torrent_ids))
        )
        result: dict[TorrentId, dict] = {}
        for (
            tid,
            show_id,
            show_name,
            show_year,
            metadata_provider,
            season_n,
            ep_n,
            vtag,
        ) in (await self.db.execute(stmt)).all():
            ctx = result.setdefault(
                tid,
                {
                    "show_id": show_id,
                    "show_name": show_name,
                    "show_year": show_year,
                    "metadata_provider": metadata_provider,
                    "seasons": set(),
                    "episodes": set(),
                    "variant": vtag or "",
                },
            )
            ctx["seasons"].add(season_n)
            ctx["episodes"].add(ep_n)
        return result

    async def get_movie_contexts_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, dict]:
        """Batch resolve movie context + variant per torrent. One query."""
        if not torrent_ids:
            return {}
        stmt = (
            select(
                MovieFile.torrent_id,
                Movie.id,
                Movie.name,
                Movie.year,
                Movie.metadata_provider,
                MovieFile.variant,
            )
            .join(Movie, MovieFile.movie_id == Movie.id)
            .where(MovieFile.torrent_id.in_(torrent_ids))
        )
        result: dict[TorrentId, dict] = {}
        for tid, movie_id, movie_name, movie_year, metadata_provider, vtag in (
            await self.db.execute(stmt)
        ).all():
            if tid in result:
                continue  # first row wins for variant
            result[tid] = {
                "movie_id": movie_id,
                "movie_name": movie_name,
                "movie_year": movie_year,
                "metadata_provider": metadata_provider,
                "variant": vtag or "",
            }
        return result

    async def get_episode_label_lookup_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, dict]:
        """Map each torrent to ``episode_id -> (season_number, episode_number)``."""
        if not torrent_ids:
            return {}
        stmt = (
            select(
                EpisodeFile.torrent_id,
                Episode.id,
                Season.number,
                Episode.number,
            )
            .join(Episode, EpisodeFile.episode_id == Episode.id)
            .join(Season, Episode.season_id == Season.id)
            .where(EpisodeFile.torrent_id.in_(torrent_ids))
        )
        result: dict[TorrentId, dict] = {}
        for tid, episode_id, season_n, ep_n in (await self.db.execute(stmt)).all():
            result.setdefault(tid, {})[episode_id] = (season_n, ep_n)
        return result

    async def get_import_status_aggregates_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, list]:
        """Return aggregated import-status rows per torrent in two queries.

        Each value is a list of ``(import_status, import_error, last_attempt_at)``
        tuples — the columns ``compute_import_progress`` actually consumes.
        Avoids re-fetching full EpisodeFile / MovieFile rows.
        """
        if not torrent_ids:
            return {}
        result: dict[TorrentId, list] = {tid: [] for tid in torrent_ids}
        ep_stmt = select(
            EpisodeFile.torrent_id,
            EpisodeFile.import_status,
            EpisodeFile.import_error,
            EpisodeFile.last_attempt_at,
        ).where(EpisodeFile.torrent_id.in_(torrent_ids))
        for tid, st, err, attempt in (await self.db.execute(ep_stmt)).all():
            result.setdefault(tid, []).append((st, err, attempt))
        mv_stmt = select(
            MovieFile.torrent_id,
            MovieFile.import_status,
            MovieFile.import_error,
            MovieFile.last_attempt_at,
        ).where(MovieFile.torrent_id.in_(torrent_ids))
        for tid, st, err, attempt in (await self.db.execute(mv_stmt)).all():
            result.setdefault(tid, []).append((st, err, attempt))
        return result

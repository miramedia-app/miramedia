"""Viewing-sync dry-run persistence."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.movies.models import Movie
from miramedia.playback.bulk import (
    BULK_CHUNK_SIZE as _BULK_CHUNK_SIZE,
)
from miramedia.playback.bulk import (
    chunked as _chunked,
)
from miramedia.shows.models import Episode, Season, Show
from miramedia.viewing_sync.matcher import (
    EpisodeLike,
    MediaCatalog,
    MovieLike,
    SeasonLike,
    ShowLike,
    build_media_catalog,
)
from miramedia.viewing_sync.models import (
    ViewingSyncCursor,
    ViewingSyncProposal,
    ViewingSyncQuarantine,
    ViewingSyncRun,
)
from miramedia.viewing_sync.schemas import QuarantineRecord, ViewingProposal

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConnectorItemKey:
    connector: str
    connector_user_id: str
    connector_item_id: str


@dataclass(frozen=True, slots=True)
class _MovieRow:
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


@dataclass(frozen=True, slots=True)
class _ShowRow:
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


@dataclass(frozen=True, slots=True)
class _SeasonRow:
    id: UUID
    show_id: UUID
    number: int


@dataclass(frozen=True, slots=True)
class _EpisodeRow:
    id: UUID
    season_id: UUID
    number: int


class ViewingSyncRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def load_media_catalog(self) -> MediaCatalog:
        movie_rows = [
            _MovieRow(*row)
            for row in (
                await self.db.execute(
                    select(
                        Movie.id,
                        Movie.imdb_id,
                        Movie.external_id,
                        Movie.metadata_provider,
                    )
                )
            ).all()
        ]
        show_rows = [
            _ShowRow(*row)
            for row in (
                await self.db.execute(
                    select(
                        Show.id,
                        Show.imdb_id,
                        Show.external_id,
                        Show.metadata_provider,
                    )
                )
            ).all()
        ]
        season_rows = [
            _SeasonRow(*row)
            for row in (
                await self.db.execute(select(Season.id, Season.show_id, Season.number))
            ).all()
        ]
        episode_rows = [
            _EpisodeRow(*row)
            for row in (
                await self.db.execute(
                    select(Episode.id, Episode.season_id, Episode.number)
                )
            ).all()
        ]
        return build_media_catalog(
            movies=cast(Sequence[MovieLike], movie_rows),
            shows=cast(Sequence[ShowLike], show_rows),
            seasons=cast(Sequence[SeasonLike], season_rows),
            episodes=cast(Sequence[EpisodeLike], episode_rows),
        )

    async def start_run(self, connector: str) -> ViewingSyncRun:
        run = ViewingSyncRun(
            id=uuid4(), connector=connector, status="running", metrics={}
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        metrics: dict[str, int],
        error_redacted: str | None = None,
    ) -> None:
        run = await self.db.get(ViewingSyncRun, run_id)
        if run is None:
            return
        run.status = status
        run.metrics = metrics
        run.error_redacted = error_redacted
        run.finished_at = datetime.now(UTC)

    async def get_user_cursors(
        self, connector: str, connector_user_ids: list[str]
    ) -> dict[str, datetime | None]:
        if not connector_user_ids:
            return {}
        stmt = select(ViewingSyncCursor).where(
            ViewingSyncCursor.connector == connector,
            ViewingSyncCursor.connector_user_id.in_(connector_user_ids),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        by_user = {row.connector_user_id: row.min_last_played_date for row in rows}
        return {user_id: by_user.get(user_id) for user_id in connector_user_ids}

    async def set_user_cursor(
        self,
        connector: str,
        connector_user_id: str,
        min_last_played_date: datetime | None,
    ) -> None:
        stmt = (
            insert(ViewingSyncCursor)
            .values(
                connector=connector,
                connector_user_id=connector_user_id,
                min_last_played_date=min_last_played_date,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[
                    ViewingSyncCursor.connector,
                    ViewingSyncCursor.connector_user_id,
                ],
                set_={
                    "min_last_played_date": min_last_played_date,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        await self.db.execute(stmt)

    async def get_prior_digest(
        self,
        *,
        connector: str,
        connector_user_id: str,
        connector_item_id: str,
    ) -> str | None:
        stmt = (
            select(ViewingSyncProposal.payload_digest)
            .where(
                ViewingSyncProposal.connector == connector,
                ViewingSyncProposal.connector_user_id == connector_user_id,
                ViewingSyncProposal.connector_item_id == connector_item_id,
            )
            .order_by(ViewingSyncProposal.created_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def bulk_get_prior_digests(
        self,
        keys: Sequence[ConnectorItemKey],
        *,
        chunk_size: int = _BULK_CHUNK_SIZE,
    ) -> dict[ConnectorItemKey, str]:
        if not keys:
            return {}

        unique_keys = list(dict.fromkeys(keys))
        latest: dict[ConnectorItemKey, str] = {}

        for chunk in _chunked(unique_keys, chunk_size):
            key_tuples = [
                (key.connector, key.connector_user_id, key.connector_item_id)
                for key in chunk
            ]
            stmt = (
                select(
                    ViewingSyncProposal.connector,
                    ViewingSyncProposal.connector_user_id,
                    ViewingSyncProposal.connector_item_id,
                    ViewingSyncProposal.payload_digest,
                    ViewingSyncProposal.created_at,
                )
                .where(
                    tuple_(
                        ViewingSyncProposal.connector,
                        ViewingSyncProposal.connector_user_id,
                        ViewingSyncProposal.connector_item_id,
                    ).in_(key_tuples)
                )
                .order_by(ViewingSyncProposal.created_at.asc())
            )
            for (
                connector,
                connector_user_id,
                connector_item_id,
                payload_digest,
                _created_at,
            ) in (await self.db.execute(stmt)).all():
                item_key = ConnectorItemKey(
                    connector=connector,
                    connector_user_id=connector_user_id,
                    connector_item_id=connector_item_id,
                )
                latest[item_key] = payload_digest

        return latest

    async def insert_proposal(self, run_id: UUID, proposal: ViewingProposal) -> bool:
        row = ViewingSyncProposal(
            id=uuid4(),
            run_id=run_id,
            connector=proposal.connector,
            connector_user_id=proposal.connector_user_id,
            connector_item_id=proposal.connector_item_id,
            miramedia_user_id=proposal.miramedia_user_id,
            media_kind=proposal.media_kind.value if proposal.media_kind else None,
            media_id=proposal.media_id,
            file_id=proposal.file_id,
            action=proposal.action.value,
            reason=proposal.reason,
            match_confidence=(
                proposal.match_confidence.value if proposal.match_confidence else None
            ),
            conflict_reason=proposal.conflict_reason,
            payload_digest=proposal.payload_digest,
            position_ms=proposal.position_ms,
            duration_ms=proposal.duration_ms,
            completed=proposal.completed,
            remote_at=proposal.remote_at,
        )
        self.db.add(row)
        await self.db.flush()
        return True

    async def insert_proposals_batch(
        self,
        run_id: UUID,
        proposals: Sequence[ViewingProposal],
        *,
        chunk_size: int = _BULK_CHUNK_SIZE,
    ) -> int:
        if not proposals:
            return 0

        inserted = 0
        for chunk in _chunked(proposals, chunk_size):
            for proposal in chunk:
                self.db.add(
                    ViewingSyncProposal(
                        id=uuid4(),
                        run_id=run_id,
                        connector=proposal.connector,
                        connector_user_id=proposal.connector_user_id,
                        connector_item_id=proposal.connector_item_id,
                        miramedia_user_id=proposal.miramedia_user_id,
                        media_kind=(
                            proposal.media_kind.value if proposal.media_kind else None
                        ),
                        media_id=proposal.media_id,
                        file_id=proposal.file_id,
                        action=proposal.action.value,
                        reason=proposal.reason,
                        match_confidence=(
                            proposal.match_confidence.value
                            if proposal.match_confidence
                            else None
                        ),
                        conflict_reason=proposal.conflict_reason,
                        payload_digest=proposal.payload_digest,
                        position_ms=proposal.position_ms,
                        duration_ms=proposal.duration_ms,
                        completed=proposal.completed,
                        remote_at=proposal.remote_at,
                    )
                )
                inserted += 1
            await self.db.flush()
        return inserted

    async def insert_quarantine(self, run_id: UUID, record: QuarantineRecord) -> None:
        self.db.add(
            ViewingSyncQuarantine(
                id=uuid4(),
                run_id=run_id,
                reason=record.reason.value,
                connector_user_id=record.connector_user_id,
                connector_item_id=record.connector_item_id,
                item_type=record.item_type,
                provider_ids=dict(record.provider_ids),
                candidate_mira_ids=[str(value) for value in record.candidate_mira_ids],
                title=record.title,
                year=record.year,
                series_name=record.series_name,
                season=record.season,
                episode=record.episode,
            )
        )

    async def insert_quarantines_batch(
        self,
        run_id: UUID,
        records: Sequence[QuarantineRecord],
        *,
        chunk_size: int = _BULK_CHUNK_SIZE,
    ) -> None:
        if not records:
            return

        for chunk in _chunked(records, chunk_size):
            for record in chunk:
                self.db.add(
                    ViewingSyncQuarantine(
                        id=uuid4(),
                        run_id=run_id,
                        reason=record.reason.value,
                        connector_user_id=record.connector_user_id,
                        connector_item_id=record.connector_item_id,
                        item_type=record.item_type,
                        provider_ids=dict(record.provider_ids),
                        candidate_mira_ids=[
                            str(value) for value in record.candidate_mira_ids
                        ],
                        title=record.title,
                        year=record.year,
                        series_name=record.series_name,
                        season=record.season,
                        episode=record.episode,
                    )
                )
            await self.db.flush()

    async def purge_stale_rows(
        self,
        *,
        retention_days: int,
        retention_min_rows: int,
    ) -> None:
        if retention_days <= 0:
            return
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        for model in (ViewingSyncProposal, ViewingSyncQuarantine):
            count_stmt = select(func.count()).select_from(model)
            total = int((await self.db.execute(count_stmt)).scalar_one())
            if total <= retention_min_rows:
                continue
            await self.db.execute(delete(model).where(model.created_at < cutoff))

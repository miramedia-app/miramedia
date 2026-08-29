"""Imported file selection for viewing-state proposals (design 386 §3.3)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import MovieFile
from miramedia.playback.bulk import (
    BULK_CHUNK_SIZE as _BULK_CHUNK_SIZE,
)
from miramedia.playback.bulk import (
    UserMediaKey,
)
from miramedia.playback.bulk import (
    chunked as _chunked,
)
from miramedia.playback.models import PlaybackProgress as PlaybackProgressRow
from miramedia.playback.schemas import MediaKind as PlaybackMediaKind
from miramedia.shows.models import EpisodeFile

_FILE_MODEL = MovieFile | EpisodeFile


@dataclass(frozen=True, slots=True)
class PlayableFile:
    file_id: UUID
    media_kind: PlaybackMediaKind


def _playable_rank(
    *,
    in_progress: bool,
    progress_updated_at: datetime | None,
    imported_at: datetime | None,
    file_id: UUID,
) -> tuple[int, float, float, str]:
    return (
        0 if in_progress else 1,
        -(progress_updated_at.timestamp() if progress_updated_at else 0.0),
        -(imported_at.timestamp() if imported_at else 0.0),
        str(file_id),
    )


def _pick_best_playable(
    candidates: Sequence[tuple[_FILE_MODEL, PlaybackProgressRow | None]],
    *,
    media_kind: PlaybackMediaKind,
) -> PlayableFile | None:
    if not candidates:
        return None
    best_file, _best_progress = min(
        candidates,
        key=lambda pair: _playable_rank(
            in_progress=(
                pair[1] is not None and pair[1].id is not None and not pair[1].completed
            ),
            progress_updated_at=pair[1].updated_at if pair[1] is not None else None,
            imported_at=pair[0].imported_at,
            file_id=pair[0].id,
        ),
    )
    return PlayableFile(file_id=best_file.id, media_kind=media_kind)


async def pick_playable_file(
    db: AsyncSession,
    *,
    user_id: UUID,
    media_kind: PlaybackMediaKind,
    media_id: UUID,
) -> PlayableFile | None:
    if media_kind == PlaybackMediaKind.movie:
        stmt = (
            select(MovieFile, PlaybackProgressRow)
            .outerjoin(
                PlaybackProgressRow,
                (PlaybackProgressRow.movie_file_id == MovieFile.id)
                & (PlaybackProgressRow.user_id == user_id),
            )
            .where(
                MovieFile.movie_id == media_id,
                MovieFile.import_status == ImportOutcome.imported,
            )
            .order_by(
                (
                    PlaybackProgressRow.id.is_not(None)
                    & (~PlaybackProgressRow.completed)
                ).desc(),
                PlaybackProgressRow.updated_at.desc().nullslast(),
                MovieFile.imported_at.desc().nullslast(),
                MovieFile.id,
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if row is None:
            return None
        movie_file, _progress = row
        return PlayableFile(file_id=movie_file.id, media_kind=PlaybackMediaKind.movie)

    stmt = (
        select(EpisodeFile, PlaybackProgressRow)
        .outerjoin(
            PlaybackProgressRow,
            (PlaybackProgressRow.episode_file_id == EpisodeFile.id)
            & (PlaybackProgressRow.user_id == user_id),
        )
        .where(
            EpisodeFile.episode_id == media_id,
            EpisodeFile.import_status == ImportOutcome.imported,
        )
        .order_by(
            (
                PlaybackProgressRow.id.is_not(None) & (~PlaybackProgressRow.completed)
            ).desc(),
            PlaybackProgressRow.updated_at.desc().nullslast(),
            EpisodeFile.imported_at.desc().nullslast(),
            EpisodeFile.id,
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    episode_file, _progress = row
    return PlayableFile(file_id=episode_file.id, media_kind=PlaybackMediaKind.episode)


async def bulk_pick_playable_files(
    db: AsyncSession,
    keys: Sequence[UserMediaKey],
    *,
    chunk_size: int = _BULK_CHUNK_SIZE,
) -> dict[UserMediaKey, PlayableFile]:
    if not keys:
        return {}

    unique_keys = list(dict.fromkeys(keys))
    picked: dict[UserMediaKey, PlayableFile] = {}

    for chunk in _chunked(unique_keys, chunk_size):
        movie_keys = [key for key in chunk if key.media_kind == PlaybackMediaKind.movie]
        episode_keys = [
            key for key in chunk if key.media_kind == PlaybackMediaKind.episode
        ]

        if movie_keys:
            movie_ids = {key.media_id for key in movie_keys}
            user_ids = {key.user_id for key in movie_keys}
            keys_by_movie: dict[UUID, list[UserMediaKey]] = defaultdict(list)
            for key in movie_keys:
                keys_by_movie[key.media_id].append(key)

            stmt = (
                select(MovieFile, PlaybackProgressRow)
                .outerjoin(
                    PlaybackProgressRow,
                    (PlaybackProgressRow.movie_file_id == MovieFile.id)
                    & (PlaybackProgressRow.user_id.in_(user_ids)),
                )
                .where(
                    MovieFile.movie_id.in_(movie_ids),
                    MovieFile.import_status == ImportOutcome.imported,
                )
            )
            candidates_by_key: dict[
                UserMediaKey, list[tuple[MovieFile, PlaybackProgressRow | None]]
            ] = defaultdict(list)
            for movie_file, progress in (await db.execute(stmt)).all():
                for key in keys_by_movie.get(movie_file.movie_id, ()):
                    matched_progress = (
                        progress
                        if progress is not None and progress.user_id == key.user_id
                        else None
                    )
                    candidates_by_key[key].append((movie_file, matched_progress))
            for key in movie_keys:
                playable = _pick_best_playable(
                    candidates_by_key.get(key, ()),
                    media_kind=PlaybackMediaKind.movie,
                )
                if playable is not None:
                    picked[key] = playable

        if episode_keys:
            episode_ids = {key.media_id for key in episode_keys}
            user_ids = {key.user_id for key in episode_keys}
            keys_by_episode: dict[UUID, list[UserMediaKey]] = defaultdict(list)
            for key in episode_keys:
                keys_by_episode[key.media_id].append(key)

            stmt = (
                select(EpisodeFile, PlaybackProgressRow)
                .outerjoin(
                    PlaybackProgressRow,
                    (PlaybackProgressRow.episode_file_id == EpisodeFile.id)
                    & (PlaybackProgressRow.user_id.in_(user_ids)),
                )
                .where(
                    EpisodeFile.episode_id.in_(episode_ids),
                    EpisodeFile.import_status == ImportOutcome.imported,
                )
            )
            candidates_by_key = defaultdict(list)
            for episode_file, progress in (await db.execute(stmt)).all():
                for key in keys_by_episode.get(episode_file.episode_id, ()):
                    matched_progress = (
                        progress
                        if progress is not None and progress.user_id == key.user_id
                        else None
                    )
                    candidates_by_key[key].append((episode_file, matched_progress))
            for key in episode_keys:
                playable = _pick_best_playable(
                    candidates_by_key.get(key, ()),
                    media_kind=PlaybackMediaKind.episode,
                )
                if playable is not None:
                    picked[key] = playable

    return picked

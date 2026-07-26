"""Data assembly for Sonarr v3 shim endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.database import release_session_before_external_io
from miramedia.file_status import ImportOutcome
from miramedia.shows.models import Episode, EpisodeFile, Show
from miramedia.shows.repository import ShowRepository
from miramedia.subtitles.arr_ids import get_or_create_arr_ids, resolve_arr_id
from miramedia.subtitles.arr_shim import common, shim_paths, sonarr_schemas
from miramedia.torrents.integrity import IntegrityPathLayout


@dataclass(frozen=True)
class _ResolvedShowContext:
    show: Show
    series_arr_id: int
    show_path: str
    episode_arr_ids: dict[UUID, int]
    episode_file_arr_ids: dict[UUID, int]
    episode_file_paths: dict[UUID, str]
    episode_file_sizes: dict[UUID, int]


async def _load_all_shows(db: AsyncSession) -> list[Show]:
    repo = ShowRepository(db)
    return await repo.get_all_shows_with_tree()


async def _load_show_by_uuid(db: AsyncSession, show_uuid: UUID) -> Show:
    repo = ShowRepository(db)
    show = await repo.get_show_with_tree_by_id(show_uuid)
    if show is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return show


async def _resolve_show_uuid(db: AsyncSession, series_arr_id: int) -> UUID:
    show_uuid = await resolve_arr_id(db, "series", series_arr_id)
    if show_uuid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return show_uuid


async def _resolve_episode_uuid(db: AsyncSession, episode_arr_id: int) -> UUID:
    episode_uuid = await resolve_arr_id(db, "episode", episode_arr_id)
    if episode_uuid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return episode_uuid


async def _resolve_episode_file_uuid(db: AsyncSession, file_arr_id: int) -> UUID:
    file_uuid = await resolve_arr_id(db, "episode_file", file_arr_id)
    if file_uuid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return file_uuid


async def _batch_arr_ids_for_shows(
    db: AsyncSession, shows: list[Show]
) -> tuple[dict[UUID, int], dict[UUID, int], dict[UUID, int]]:
    series_uuids, episode_uuids, file_uuids = sonarr_schemas.collect_entity_uuids(shows)
    series_ids = await get_or_create_arr_ids(db, "series", series_uuids)
    episode_ids = await get_or_create_arr_ids(db, "episode", episode_uuids)
    file_ids = await get_or_create_arr_ids(db, "episode_file", file_uuids)
    return series_ids, episode_ids, file_ids


async def _resolve_show_context(
    db: AsyncSession,
    show: Show,
    *,
    series_arr_ids: dict[UUID, int] | None = None,
    episode_arr_ids: dict[UUID, int] | None = None,
    episode_file_arr_ids: dict[UUID, int] | None = None,
) -> _ResolvedShowContext:
    shows = [show]
    if (
        series_arr_ids is None
        or episode_arr_ids is None
        or episode_file_arr_ids is None
    ):
        series_map, episode_map, file_map = await _batch_arr_ids_for_shows(db, shows)
    else:
        series_map, episode_map, file_map = (
            series_arr_ids,
            episode_arr_ids,
            episode_file_arr_ids,
        )

    layout = IntegrityPathLayout.from_config()
    show_path = shim_paths.show_root_path(layout, show)
    raw_paths = shim_paths.batch_episode_file_paths_for_show(layout, show)

    await release_session_before_external_io(db)
    sizes = await shim_paths.batch_video_sizes(
        db,
        file_ids=list(raw_paths.keys()),
        paths=raw_paths,
    )

    episode_file_paths = {
        file_id: str(path) for file_id, path in raw_paths.items() if path is not None
    }
    return _ResolvedShowContext(
        show=show,
        series_arr_id=series_map[show.id],
        show_path=str(show_path),
        episode_arr_ids=episode_map,
        episode_file_arr_ids=file_map,
        episode_file_paths=episode_file_paths,
        episode_file_sizes=sizes,
    )


async def _resolve_episode_file_context(
    db: AsyncSession,
    show: Show,
    episode: Episode,
    episode_files: Sequence[EpisodeFile],
) -> _ResolvedShowContext:
    series_map = await get_or_create_arr_ids(db, "series", [show.id])
    episode_map = await get_or_create_arr_ids(db, "episode", [episode.id])
    imported_file_ids = [
        episode_file.id
        for episode_file in episode_files
        if episode_file.import_status == ImportOutcome.imported
    ]
    file_map = await get_or_create_arr_ids(db, "episode_file", imported_file_ids)

    layout = IntegrityPathLayout.from_config()
    show_path = shim_paths.show_root_path_from_scalar_columns(layout, show)
    raw_paths = shim_paths.batch_episode_file_paths_for_episode(
        layout, show, episode, episode_files
    )

    await release_session_before_external_io(db)
    sizes = await shim_paths.batch_video_sizes(
        db,
        file_ids=list(raw_paths.keys()),
        paths=raw_paths,
    )

    episode_file_paths = {
        file_id: str(path) for file_id, path in raw_paths.items() if path is not None
    }
    return _ResolvedShowContext(
        show=show,
        series_arr_id=series_map[show.id],
        show_path=str(show_path),
        episode_arr_ids=episode_map,
        episode_file_arr_ids=file_map,
        episode_file_paths=episode_file_paths,
        episode_file_sizes=sizes,
    )


def _episode_file_payload(
    ctx: _ResolvedShowContext,
    episode_file: EpisodeFile,
) -> dict | None:
    path = ctx.episode_file_paths.get(episode_file.id)
    size = ctx.episode_file_sizes.get(episode_file.id)
    arr_id = ctx.episode_file_arr_ids.get(episode_file.id)
    # Keep this gate identical to ``sonarr_schemas.episode_json``'s servability check.
    if path is None or size is None or arr_id is None or size <= 0:
        return None
    return sonarr_schemas.episode_file_json(
        episode_file,
        arr_id=arr_id,
        path=path,
        size=size,
    )


async def list_series(db: AsyncSession) -> list[dict]:
    shows = await _load_all_shows(db)
    if not shows:
        return []

    series_map = await get_or_create_arr_ids(db, "series", [show.id for show in shows])
    layout = IntegrityPathLayout.from_config()
    await release_session_before_external_io(db)
    show_paths = shim_paths.batch_show_root_paths(layout, shows)
    return [
        sonarr_schemas.series_json(
            show,
            arr_id=series_map[show.id],
            path=show_paths[show.id],
        )
        for show in shows
    ]


async def get_series(db: AsyncSession, series_arr_id: int) -> dict:
    show_uuid = await _resolve_show_uuid(db, series_arr_id)
    show = await _load_show_by_uuid(db, show_uuid)
    ctx = await _resolve_show_context(db, show)
    return sonarr_schemas.series_json(
        ctx.show,
        arr_id=ctx.series_arr_id,
        path=ctx.show_path,
    )


async def list_episodes(
    db: AsyncSession,
    *,
    series_arr_id: int,
    include_episode_file: bool,
) -> list[dict]:
    show_uuid = await _resolve_show_uuid(db, series_arr_id)
    show = await _load_show_by_uuid(db, show_uuid)
    ctx = await _resolve_show_context(db, show)

    episodes: list[dict] = []
    for season, episode in sonarr_schemas.iter_show_episodes(show):
        primary_file = sonarr_schemas.pick_primary_imported_file(episode)
        episodes.append(
            sonarr_schemas.episode_json(
                episode,
                arr_id=ctx.episode_arr_ids[episode.id],
                series_arr_id=ctx.series_arr_id,
                season_number=season.number,
                include_episode_file=include_episode_file,
                episode_file=primary_file,
                episode_file_arr_id=(
                    ctx.episode_file_arr_ids.get(primary_file.id)
                    if primary_file is not None
                    else None
                ),
                episode_file_path=(
                    ctx.episode_file_paths.get(primary_file.id)
                    if primary_file is not None
                    else None
                ),
                episode_file_size=(
                    ctx.episode_file_sizes.get(primary_file.id)
                    if primary_file is not None
                    else None
                ),
            )
        )
    return episodes


async def get_episode(
    db: AsyncSession,
    episode_arr_id: int,
    *,
    include_episode_file: bool = False,
) -> dict:
    episode_uuid = await _resolve_episode_uuid(db, episode_arr_id)
    repo = ShowRepository(db)
    episode = await repo.get_episode_with_show_tree(episode_uuid)
    if episode is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    show = episode.season.show
    ctx = await _resolve_episode_file_context(db, show, episode, episode.episode_files)
    primary_file = sonarr_schemas.pick_primary_imported_file(episode)
    return sonarr_schemas.episode_json(
        episode,
        arr_id=ctx.episode_arr_ids[episode.id],
        series_arr_id=ctx.series_arr_id,
        season_number=episode.season.number,
        include_episode_file=include_episode_file,
        episode_file=primary_file,
        episode_file_arr_id=(
            ctx.episode_file_arr_ids.get(primary_file.id)
            if primary_file is not None
            else None
        ),
        episode_file_path=(
            ctx.episode_file_paths.get(primary_file.id)
            if primary_file is not None
            else None
        ),
        episode_file_size=(
            ctx.episode_file_sizes.get(primary_file.id)
            if primary_file is not None
            else None
        ),
    )


async def list_episode_files(db: AsyncSession, *, series_arr_id: int) -> list[dict]:
    show_uuid = await _resolve_show_uuid(db, series_arr_id)
    show = await _load_show_by_uuid(db, show_uuid)
    ctx = await _resolve_show_context(db, show)

    files: list[dict] = []
    for season in show.seasons:
        for episode in season.episodes:
            for episode_file in sonarr_schemas.imported_episode_files(episode):
                payload = _episode_file_payload(ctx, episode_file)
                if payload is not None:
                    files.append(payload)
    return files


async def get_episode_file(db: AsyncSession, file_arr_id: int) -> dict:
    file_uuid = await _resolve_episode_file_uuid(db, file_arr_id)
    repo = ShowRepository(db)
    episode_file = await repo.get_episode_file_with_show_tree(file_uuid)
    if episode_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    show = episode_file.episode.season.show
    ctx = await _resolve_episode_file_context(
        db, show, episode_file.episode, [episode_file]
    )
    payload = _episode_file_payload(ctx, episode_file)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return payload


def list_rootfolders() -> list[dict]:
    roots = shim_paths.show_library_roots()
    return shim_paths.rootfolder_payloads(roots)


def list_tags() -> list[dict]:
    return common.list_tags()


def list_history() -> list[dict]:
    return common.list_history()

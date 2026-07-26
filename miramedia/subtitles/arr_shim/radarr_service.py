"""Data assembly for Radarr v3 shim endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.database import release_session_before_external_io
from miramedia.movies.models import Movie
from miramedia.movies.repository import MovieRepository
from miramedia.subtitles.arr_ids import get_or_create_arr_ids, resolve_arr_id
from miramedia.subtitles.arr_shim import common, radarr_schemas, shim_paths
from miramedia.torrents.integrity import IntegrityPathLayout


@dataclass(frozen=True)
class _ResolvedMovieContext:
    movie: Movie
    movie_arr_id: int
    movie_path: str
    movie_file_arr_ids: dict[UUID, int]
    movie_file_paths: dict[UUID, str]
    movie_file_sizes: dict[UUID, int]


async def _load_all_movies(db: AsyncSession) -> list[Movie]:
    repo = MovieRepository(db)
    return await repo.get_all_movies_with_files()


async def _load_movie_by_uuid(db: AsyncSession, movie_uuid: UUID) -> Movie:
    repo = MovieRepository(db)
    movie = await repo.get_movie_with_files_by_id(movie_uuid)
    if movie is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return movie


async def _resolve_movie_uuid(db: AsyncSession, movie_arr_id: int) -> UUID:
    movie_uuid = await resolve_arr_id(db, "movie", movie_arr_id)
    if movie_uuid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return movie_uuid


async def _batch_arr_ids_for_movies(
    db: AsyncSession, movies: list[Movie]
) -> tuple[dict[UUID, int], dict[UUID, int]]:
    movie_uuids, file_uuids = radarr_schemas.collect_entity_uuids(movies)
    movie_ids = await get_or_create_arr_ids(db, "movie", movie_uuids)
    file_ids = await get_or_create_arr_ids(db, "movie_file", file_uuids)
    return movie_ids, file_ids


async def _resolve_movie_context(
    db: AsyncSession,
    movie: Movie,
    *,
    movie_arr_ids: dict[UUID, int] | None = None,
    movie_file_arr_ids: dict[UUID, int] | None = None,
) -> _ResolvedMovieContext:
    movies = [movie]
    if movie_arr_ids is None or movie_file_arr_ids is None:
        movie_map, file_map = await _batch_arr_ids_for_movies(db, movies)
    else:
        movie_map, file_map = movie_arr_ids, movie_file_arr_ids

    layout = IntegrityPathLayout.from_config()
    movie_path = shim_paths.movie_root_path(layout, movie)
    raw_paths = shim_paths.batch_movie_file_paths_for_movie(layout, movie)

    await release_session_before_external_io(db)
    sizes = await shim_paths.batch_video_sizes(
        db,
        file_ids=list(raw_paths.keys()),
        paths=raw_paths,
    )

    movie_file_paths = {
        file_id: str(path) for file_id, path in raw_paths.items() if path is not None
    }
    return _ResolvedMovieContext(
        movie=movie,
        movie_arr_id=movie_map[movie.id],
        movie_path=str(movie_path),
        movie_file_arr_ids=file_map,
        movie_file_paths=movie_file_paths,
        movie_file_sizes=sizes,
    )


def _movie_payload(ctx: _ResolvedMovieContext) -> dict:
    primary_file = radarr_schemas.pick_best_imported_file(ctx.movie)
    return radarr_schemas.movie_json(
        ctx.movie,
        arr_id=ctx.movie_arr_id,
        path=ctx.movie_path,
        movie_file=primary_file,
        movie_file_arr_id=(
            ctx.movie_file_arr_ids.get(primary_file.id) if primary_file else None
        ),
        movie_file_path=(
            ctx.movie_file_paths.get(primary_file.id) if primary_file else None
        ),
        movie_file_size=(
            ctx.movie_file_sizes.get(primary_file.id) if primary_file else None
        ),
    )


async def list_movies(db: AsyncSession) -> list[dict]:
    movies = await _load_all_movies(db)
    if not movies:
        return []

    movie_map, file_map = await _batch_arr_ids_for_movies(db, movies)
    primary_files = {
        movie.id: radarr_schemas.pick_best_imported_file(movie) for movie in movies
    }
    layout = IntegrityPathLayout.from_config()
    await release_session_before_external_io(db)
    movie_paths, raw_paths = await asyncio.gather(
        asyncio.to_thread(shim_paths.batch_movie_root_paths, layout, movies),
        asyncio.to_thread(shim_paths.batch_movie_file_paths_for_movies, layout, movies),
    )

    primary_file_ids = [
        movie_file.id for movie_file in primary_files.values() if movie_file is not None
    ]
    sizes = await shim_paths.batch_video_sizes(
        db,
        file_ids=primary_file_ids,
        paths=raw_paths,
    )
    movie_file_paths = {
        file_id: str(path) for file_id, path in raw_paths.items() if path is not None
    }

    payloads: list[dict] = []
    for movie in movies:
        primary_file = primary_files[movie.id]
        payloads.append(
            radarr_schemas.movie_json(
                movie,
                arr_id=movie_map[movie.id],
                path=movie_paths[movie.id],
                movie_file=primary_file,
                movie_file_arr_id=(
                    file_map.get(primary_file.id) if primary_file else None
                ),
                movie_file_path=(
                    movie_file_paths.get(primary_file.id) if primary_file else None
                ),
                movie_file_size=(sizes.get(primary_file.id) if primary_file else None),
            )
        )
    return payloads


async def get_movie(db: AsyncSession, movie_arr_id: int) -> dict:
    movie_uuid = await _resolve_movie_uuid(db, movie_arr_id)
    movie = await _load_movie_by_uuid(db, movie_uuid)
    ctx = await _resolve_movie_context(db, movie)
    return _movie_payload(ctx)


def list_rootfolders() -> list[dict]:
    roots = shim_paths.movie_library_roots()
    return shim_paths.rootfolder_payloads(roots)


def list_tags() -> list[dict]:
    return common.list_tags()


def list_history() -> list[dict]:
    return common.list_history()

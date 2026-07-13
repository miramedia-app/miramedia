from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.responses import Response

from miramedia.auth.users import current_active_user
from miramedia.config import MiraMediaConfig
from miramedia.database import DbSessionDependency, release_session_before_external_io
from miramedia.exceptions import NotFoundError
from miramedia.media_inventory import find_inventory_path, upsert_inventory_path
from miramedia.movies.dependencies import movie_dep, movie_service_dep
from miramedia.naming import (
    episode_file_stem_candidates,
    movie_file_stem_candidates,
)
from miramedia.shows.dependencies import show_service_dep
from miramedia.shows.schemas import EpisodeId
from miramedia.shows.service import ShowService
from miramedia.streams.transcode import (
    HlsTranscodeError,
    can_direct_play,
    ensure_hls_playlist,
    hls_playlist_ready,
    hls_transcode_available,
    schedule_hls_warm,
    segment_dir,
)
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.utils import resolve_within

if TYPE_CHECKING:
    from miramedia.movies.schemas import Movie, MovieFile
    from miramedia.movies.service import MovieService
    from miramedia.shows.schemas import EpisodeFile

log = logging.getLogger(__name__)


class StreamProbeResponse(BaseModel):
    direct_play: bool
    container: str
    hls_playlist_url: str | None = None


router = APIRouter(
    prefix="/streams",
    tags=["streams"],
    dependencies=[Depends(current_active_user)],
)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts", ".wmv"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}
_VTT_CACHE: TTLCache = TTLCache(maxsize=512, ttl=3600)
_MAX_SRT_BYTES = 5 * 1024 * 1024

FileIdQuery = Annotated[UUID, Query()]
DownloadQuery = Annotated[bool, Query()]


def _find_video_file(directory: Path, file_stem: str) -> Path | None:
    """Find a video file matching the given stem in the directory."""
    if not directory.exists():
        return None
    for ext in VIDEO_EXTENSIONS:
        candidate = directory / f"{file_stem}{ext}"
        if candidate.exists():
            return candidate
    # Fallback: glob for files starting with the stem
    for f in directory.iterdir():
        if (
            f.is_file()
            and f.suffix.lower() in VIDEO_EXTENSIONS
            and f.name.lower().startswith(file_stem.lower())
        ):
            return f
    return None


def _find_first_video_file(directory: Path, file_stems: list[str]) -> Path | None:
    for stem in file_stems:
        match = _find_video_file(directory, stem)
        if match is not None:
            return match
    return None


async def _find_indexed_file(
    db: DbSessionDependency,
    *,
    file_id: UUID,
    kind: str,
    language: str = "",
) -> Path | None:
    return await find_inventory_path(
        db,
        file_id=file_id,
        kind=kind,
        language=language,
    )


async def _remember_indexed_file(
    db: DbSessionDependency,
    *,
    file_id: UUID,
    kind: str,
    media_type: str,
    path: Path,
    language: str = "",
) -> None:
    await upsert_inventory_path(
        db,
        file_id=file_id,
        kind=kind,
        language=language,
        media_type=media_type,
        path=path,
    )


def _validate_media_path(file_path: Path, allowed_roots: list[Path]) -> None:
    """Ensure the resolved path is within an allowed media directory."""
    resolved = file_path.resolve()
    for root in allowed_roots:
        if resolved.is_relative_to(root.resolve()):
            return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")


def _serve_file(file_path: Path, download: bool = False) -> FileResponse:
    """Return a FileResponse with appropriate content type."""
    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = "application/octet-stream"

    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{file_path.name}"'

    headers.setdefault("Cache-Control", "private, max-age=3600")
    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers=headers or None,
    )


def _hls_playlist_url(media_kind: str, media_id: str, file_id: UUID) -> str:
    return f"/api/v1/streams/{media_kind}/{media_id}/hls/index.m3u8?file_id={file_id}"


def _probe_hls_playlist_url(
    *,
    direct_play: bool,
    media_kind: str,
    media_id: str,
    file_id: UUID,
    source_file: Path,
) -> str | None:
    """Only advertise HLS when the cache is warm — never block playback on encode."""
    if direct_play or not hls_transcode_available():
        return None
    if hls_playlist_ready(source_file):
        return _hls_playlist_url(media_kind, media_id, file_id)
    schedule_hls_warm(source_file)
    return None


@router.get("/movies/{movie_id}/probe")
async def probe_movie_stream(
    movie: movie_dep,
    movie_service: movie_service_dep,
    db: DbSessionDependency,
    file_id: FileIdQuery,
) -> StreamProbeResponse:
    """Tell the player whether to use direct range streaming or HLS."""
    movie_file = await _load_movie_file(movie_service=movie_service, file_id=file_id)
    video_file = await _resolve_movie_video_file(
        movie=movie,
        movie_service=movie_service,
        movie_file=movie_file,
        db=db,
    )
    direct = can_direct_play(video_file)
    return StreamProbeResponse(
        direct_play=direct,
        container=video_file.suffix.lower().lstrip("."),
        hls_playlist_url=_probe_hls_playlist_url(
            direct_play=direct,
            media_kind="movies",
            media_id=str(movie.id),
            file_id=file_id,
            source_file=video_file,
        ),
    )


@router.get("/episodes/{episode_id}/probe")
async def probe_episode_stream(
    episode_id: EpisodeId,
    show_service: show_service_dep,
    db: DbSessionDependency,
    file_id: FileIdQuery,
) -> StreamProbeResponse:
    episode_file = await _load_episode_file(show_service=show_service, file_id=file_id)
    video_file = await _resolve_episode_video_file(
        show_service=show_service,
        episode_file=episode_file,
        db=db,
    )
    direct = can_direct_play(video_file)
    return StreamProbeResponse(
        direct_play=direct,
        container=video_file.suffix.lower().lstrip("."),
        hls_playlist_url=_probe_hls_playlist_url(
            direct_play=direct,
            media_kind="episodes",
            media_id=str(episode_id),
            file_id=file_id,
            source_file=video_file,
        ),
    )


async def _resolve_video_file(
    db: DbSessionDependency,
    *,
    file_id: UUID,
    media_type: str,
    directory: Path,
    stems: list[str],
    allowed_roots: list[Path],
) -> Path:
    """Resolve an on-disk video file, validate it, and seed the inventory.

    Looks up the inventory first; on a hit the cached path is validated and
    returned without touching the DB again. On a miss the disk is scanned, the
    match is validated, and the inventory row is upserted exactly once.
    """
    cached = await _find_indexed_file(db, file_id=file_id, kind="video")
    if cached is not None:
        await asyncio.to_thread(_validate_media_path, cached, allowed_roots)
        return cached

    video_file = await asyncio.to_thread(_find_first_video_file, directory, stems)
    if video_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Video file not found on disk"
        )
    await asyncio.to_thread(_validate_media_path, video_file, allowed_roots)
    await _remember_indexed_file(
        db,
        file_id=file_id,
        kind="video",
        media_type=media_type,
        path=video_file,
    )
    return video_file


async def _load_episode_file(
    *,
    show_service: ShowService,
    file_id: UUID,
) -> EpisodeFile:
    episode_file = await show_service.show_repository.get_episode_file_by_id(file_id)
    if episode_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Episode file not found"
        )
    return episode_file


async def _load_movie_file(
    *,
    movie_service: MovieService,
    file_id: UUID,
) -> MovieFile:
    movie_file = await movie_service.movie_repository.get_movie_file_by_id(file_id)
    if movie_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Movie file not found"
        )
    return movie_file


async def _resolve_episode_video_file(
    *,
    show_service: ShowService,
    episode_file: EpisodeFile,
    db: DbSessionDependency,
) -> Path:
    """Resolve a validated, inventory-seeded on-disk episode video file."""
    config = MiraMediaConfig()
    try:
        episode = await show_service.get_episode(episode_id=episode_file.episode_id)
        season = await show_service.get_season_by_episode(
            episode_id=episode_file.episode_id
        )
        show = await show_service.show_repository.get_show_by_season_id(
            season_id=season.id
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found"
        ) from None

    season_dir = show_service.get_root_season_directory(
        show=show, season_number=season.number
    )
    parts = NameParts.from_row(episode_file)
    names = episode_file_stem_candidates(
        show,
        season_number=season.number,
        episode_number=episode.number,
        quality=episode_file.quality,
        parts=parts,
    )
    allowed_roots = [config.misc.show_directory]
    allowed_roots.extend(Path(lib.path) for lib in config.misc.show_libraries)
    return await _resolve_video_file(
        db,
        file_id=episode_file.id,
        media_type="episode",
        directory=season_dir,
        stems=names,
        allowed_roots=allowed_roots,
    )


async def _resolve_movie_video_file(
    *,
    movie: Movie,
    movie_service: MovieService,
    movie_file: MovieFile,
    db: DbSessionDependency,
) -> Path:
    """Resolve a validated, inventory-seeded on-disk movie video file."""
    config = MiraMediaConfig()
    movie_root = movie_service.get_movie_root_path(movie=movie)
    parts = NameParts.from_row(movie_file)
    names = movie_file_stem_candidates(movie, movie_file.quality, parts)
    allowed_roots = [config.misc.movie_directory]
    allowed_roots.extend(Path(lib.path) for lib in config.misc.movie_libraries)
    return await _resolve_video_file(
        db,
        file_id=movie_file.id,
        media_type="movie",
        directory=movie_root,
        stems=names,
        allowed_roots=allowed_roots,
    )


@router.get("/episodes/{episode_id}/hls/index.m3u8")
async def episode_hls_playlist(
    episode_id: EpisodeId,  # noqa: ARG001 — required by route signature
    show_service: show_service_dep,
    db: DbSessionDependency,
    file_id: FileIdQuery,
) -> FileResponse:
    episode_file = await _load_episode_file(show_service=show_service, file_id=file_id)
    video_file = await _resolve_episode_video_file(
        show_service=show_service,
        episode_file=episode_file,
        db=db,
    )
    try:
        playlist = await ensure_hls_playlist(video_file)
    except HlsTranscodeError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return FileResponse(
        playlist,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/episodes/{episode_id}/hls/{segment_name}")
async def episode_hls_segment(
    episode_id: EpisodeId,  # noqa: ARG001 — required by route signature
    show_service: show_service_dep,
    db: DbSessionDependency,
    segment_name: str,
    file_id: FileIdQuery,
) -> FileResponse:
    episode_file = await _load_episode_file(show_service=show_service, file_id=file_id)
    video_file = await _resolve_episode_video_file(
        show_service=show_service,
        episode_file=episode_file,
        db=db,
    )
    seg = resolve_within(segment_dir(video_file), segment_name)
    if seg is None or not seg.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    return FileResponse(seg, media_type="video/mp2t")


@router.get("/movies/{movie_id}/hls/index.m3u8")
async def movie_hls_playlist(
    movie: movie_dep,
    movie_service: movie_service_dep,
    db: DbSessionDependency,
    file_id: FileIdQuery,
) -> FileResponse:
    movie_file = await _load_movie_file(movie_service=movie_service, file_id=file_id)
    video_file = await _resolve_movie_video_file(
        movie=movie,
        movie_service=movie_service,
        movie_file=movie_file,
        db=db,
    )
    try:
        playlist = await ensure_hls_playlist(video_file)
    except HlsTranscodeError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return FileResponse(
        playlist,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/movies/{movie_id}/hls/{segment_name}")
async def movie_hls_segment(
    movie: movie_dep,
    movie_service: movie_service_dep,
    db: DbSessionDependency,
    segment_name: str,
    file_id: FileIdQuery,
) -> FileResponse:
    movie_file = await _load_movie_file(movie_service=movie_service, file_id=file_id)
    video_file = await _resolve_movie_video_file(
        movie=movie,
        movie_service=movie_service,
        movie_file=movie_file,
        db=db,
    )
    seg = resolve_within(segment_dir(video_file), segment_name)
    if seg is None or not seg.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segment not found")
    return FileResponse(seg, media_type="video/mp2t")


@router.get("/movies/{movie_id}")
async def stream_movie(
    movie: movie_dep,
    movie_service: movie_service_dep,
    db: DbSessionDependency,
    file_id: FileIdQuery,
    download: DownloadQuery = False,
) -> FileResponse:
    """Stream or download a movie file."""
    movie_file = await _load_movie_file(movie_service=movie_service, file_id=file_id)
    video_file = await _resolve_movie_video_file(
        movie=movie,
        movie_service=movie_service,
        movie_file=movie_file,
        db=db,
    )

    # Release the DB connection before the (potentially very long) byte stream.
    # A FileResponse holds this request's session open until the transfer
    # finishes; left idle-in-transaction past the server's
    # idle_in_transaction_session_timeout it gets reaped and the get_session
    # finalizer commit dies on a closed connection. We need no DB while serving.
    await release_session_before_external_io(db)

    log.debug(f"Streaming movie file: {video_file.name}")
    return _serve_file(video_file, download=download)


@router.get("/episodes/{episode_id}")
async def stream_episode(
    episode_id: EpisodeId,  # noqa: ARG001 — required by route signature
    show_service: show_service_dep,
    db: DbSessionDependency,
    file_id: FileIdQuery,
    download: DownloadQuery = False,
) -> FileResponse:
    """Stream or download a TV episode file."""
    episode_file = await _load_episode_file(show_service=show_service, file_id=file_id)
    video_file = await _resolve_episode_video_file(
        show_service=show_service,
        episode_file=episode_file,
        db=db,
    )

    # See ``stream_movie``: drop the DB connection before the long file stream
    # so it isn't held idle-in-transaction for the transfer's lifetime.
    await release_session_before_external_io(db)

    log.debug(f"Streaming episode file: {video_file.name}")
    return _serve_file(video_file, download=download)


def _find_subtitle_file(directory: Path, file_stem: str, language: str) -> Path | None:
    """Find a subtitle file matching the given stem and language."""
    if not directory.exists():
        return None
    for ext in SUBTITLE_EXTENSIONS:
        candidate = directory / f"{file_stem}.{language}{ext}"
        if candidate.exists():
            return candidate
    return None


def _find_first_subtitle_file(
    directory: Path, file_stems: list[str], language: str
) -> Path | None:
    for stem in file_stems:
        match = _find_subtitle_file(directory, stem, language)
        if match is not None:
            return match
    return None


async def _resolve_subtitle_file(
    db: DbSessionDependency,
    *,
    file_id: UUID,
    media_type: str,
    language: str,
    directory: Path,
    stems: list[str],
    allowed_roots: list[Path],
) -> Path:
    """Resolve an on-disk subtitle file, validate it, and seed the inventory.

    Mirrors :func:`_resolve_video_file`: an inventory hit is validated and
    returned without a DB write, while a miss scans disk, validates, and
    upserts the row exactly once.
    """
    cached = await _find_indexed_file(
        db,
        file_id=file_id,
        kind="subtitle",
        language=language,
    )
    if cached is not None:
        await asyncio.to_thread(_validate_media_path, cached, allowed_roots)
        return cached

    sub_file = await asyncio.to_thread(
        _find_first_subtitle_file, directory, stems, language
    )
    if sub_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subtitle file not found"
        )
    await asyncio.to_thread(_validate_media_path, sub_file, allowed_roots)
    await _remember_indexed_file(
        db,
        file_id=file_id,
        kind="subtitle",
        media_type=media_type,
        language=language,
        path=sub_file,
    )
    return sub_file


def _convert_srt_to_vtt(srt_path: Path) -> str:
    """Convert SRT subtitle content to WebVTT format."""
    import re

    stat = srt_path.stat()
    if stat.st_size > _MAX_SRT_BYTES:
        raise HTTPException(status_code=413, detail="Subtitle file too large")
    cache_key = (str(srt_path), stat.st_mtime_ns, stat.st_size)
    cached = _VTT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    content = srt_path.read_text(encoding="utf-8", errors="replace")
    vtt = "WEBVTT\n\n"
    vtt += re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", content)
    _VTT_CACHE[cache_key] = vtt
    return vtt


@router.get("/subtitles/movies/{movie_id}/{language}")
async def stream_movie_subtitle(
    movie: movie_dep,
    movie_service: movie_service_dep,
    db: DbSessionDependency,
    language: str,
    file_id: FileIdQuery,
) -> Response:
    """Stream a subtitle file for a movie."""
    config = MiraMediaConfig()
    movie_file = await _load_movie_file(movie_service=movie_service, file_id=file_id)
    movie_root = movie_service.get_movie_root_path(movie=movie)
    parts = NameParts.from_row(movie_file)
    movie_file_names = movie_file_stem_candidates(movie, movie_file.quality, parts)
    allowed_roots = [config.misc.movie_directory]
    allowed_roots.extend(Path(lib.path) for lib in config.misc.movie_libraries)
    sub_file = await _resolve_subtitle_file(
        db,
        file_id=movie_file.id,
        media_type="movie",
        language=language,
        directory=movie_root,
        stems=movie_file_names,
        allowed_roots=allowed_roots,
    )

    if sub_file.suffix.lower() == ".srt":
        vtt_content = await asyncio.to_thread(_convert_srt_to_vtt, sub_file)
        return Response(content=vtt_content, media_type="text/vtt")

    return _serve_file(sub_file)


@router.get("/subtitles/episodes/{episode_id}/{language}")
async def stream_episode_subtitle(
    episode_id: EpisodeId,  # noqa: ARG001 — required by route signature
    show_service: show_service_dep,
    db: DbSessionDependency,
    language: str,
    file_id: FileIdQuery,
) -> Response:
    """Stream a subtitle file for an episode."""
    config = MiraMediaConfig()
    episode_file = await _load_episode_file(show_service=show_service, file_id=file_id)

    try:
        episode = await show_service.get_episode(episode_id=episode_file.episode_id)
        season = await show_service.get_season_by_episode(
            episode_id=episode_file.episode_id
        )
        show = await show_service.show_repository.get_show_by_season_id(
            season_id=season.id
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Episode not found"
        ) from None

    season_dir = show_service.get_root_season_directory(
        show=show, season_number=season.number
    )
    parts = NameParts.from_row(episode_file)
    episode_file_names = episode_file_stem_candidates(
        show,
        season_number=season.number,
        episode_number=episode.number,
        quality=episode_file.quality,
        parts=parts,
    )
    allowed_roots = [config.misc.show_directory]
    allowed_roots.extend(Path(lib.path) for lib in config.misc.show_libraries)
    sub_file = await _resolve_subtitle_file(
        db,
        file_id=episode_file.id,
        media_type="episode",
        language=language,
        directory=season_dir,
        stems=episode_file_names,
        allowed_roots=allowed_roots,
    )

    if sub_file.suffix.lower() == ".srt":
        vtt_content = await asyncio.to_thread(_convert_srt_to_vtt, sub_file)
        return Response(content=vtt_content, media_type="text/vtt")

    return _serve_file(sub_file)

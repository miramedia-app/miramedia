"""Library scan — walk configured roots and surface unimported directories.

This is the import domain's discovery flow: a pure, side-effect-free walk +
fuzzy-match + metadata-provider lookup that returns a ``ScanResponse``. The
side-effectful parts (auto-import of strong matches, caching) live in
``imports/tasks.py``. It was extracted out of ``TorrentService`` because a
filesystem scan is not a torrent concern — its only torrent-domain input was
the ignored-paths list, which callers now pass in.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from miramedia.config import MiraMediaConfig
from miramedia.imports.scan_matching import (
    ProviderSearchCollaborator,
    ScanFilesystemCollector,
    build_existing_media_indexes,
    build_imported_stem_maps,
)
from miramedia.imports.schemas import ScanResponse
from miramedia.torrents.schemas import MediaType

if TYPE_CHECKING:
    from miramedia.movies.service import MovieService
    from miramedia.shows.service import ShowService


async def scan_libraries(
    ignored_paths: set[str],
    show_service: "ShowService | None" = None,
    movie_service: "MovieService | None" = None,
) -> ScanResponse:
    """Walk all configured library roots and surface unimported dirs.

    Returns ``ScanResponse``. Each entry is a directory containing video
    files that does NOT correspond to an already-imported show or movie
    (matched by ``[provider-id]`` tag in the dir name, or by name+year).

    Honors ``.mmignore`` files and the caller-supplied ``ignored_paths``
    (the ``ignored_import_path`` table, read by the caller).

    Session lifetime: the DB read phase (existing shows / movies) runs first;
    we then RELEASE every show/movie service reference before the multi-minute
    filesystem walk + metadata-provider HTTP fan-out. The ``show_service`` /
    ``movie_service`` args are optional — when omitted we open short-lived
    ``bg_show_service`` / ``bg_movie_service`` sessions just to snapshot the
    existing-media lists, then close them. Either way, no DB session is held
    while the filesystem collector runs.
    """
    cfg = MiraMediaConfig().misc
    imports_cfg = MiraMediaConfig().imports
    auto_pick_threshold = cfg.auto_pick_confidence_threshold
    show_roots: list[tuple[Path, str]] = [
        (Path(cfg.show_directory), "Default"),
        *((Path(lib.path), lib.name) for lib in cfg.show_libraries),
    ]
    movie_roots: list[tuple[Path, str]] = [
        (Path(cfg.movie_directory), "Default"),
        *((Path(lib.path), lib.name) for lib in cfg.movie_libraries),
    ]

    ignored_abs = {str(Path(p).absolute()) for p in ignored_paths}  # noqa: ASYNC240 — cheap stat, intentional

    if show_service is not None:
        existing_shows = await show_service.get_all_shows()
    else:
        from miramedia.database import bg_show_service

        async with bg_show_service() as _svc:
            existing_shows = await _svc.get_all_shows()
    if movie_service is not None:
        existing_movies = await movie_service.get_all_movies()
        movie_ids = [m.id for m in existing_movies]
        movie_files_by_id = (
            await movie_service.movie_repository.get_movie_files_for_movies(movie_ids)
        )
    else:
        from miramedia.database import bg_movie_service

        async with bg_movie_service() as _svc:
            existing_movies = await _svc.get_all_movies()
            movie_ids = [m.id for m in existing_movies]
            movie_files_by_id = await _svc.movie_repository.get_movie_files_for_movies(
                movie_ids
            )
    del show_service
    del movie_service

    show_imported_stems, movie_imported_stems = await asyncio.to_thread(
        build_imported_stem_maps,
        existing_shows,
        existing_movies,
        movie_files_by_id,
    )
    indexes = build_existing_media_indexes(existing_shows, existing_movies)

    from miramedia.metadata.dependencies import get_all_enabled_providers

    provider_search = ProviderSearchCollaborator(
        get_all_enabled_providers(),
        imports_cfg.provider_search_max_results,
    )

    collector_kwargs = {
        "ignored_paths": ignored_paths,
        "ignored_abs": ignored_abs,
        "existing_shows": existing_shows,
        "existing_movies": existing_movies,
        "indexes": indexes,
        "show_imported_stems": show_imported_stems,
        "movie_imported_stems": movie_imported_stems,
        "provider_search": provider_search,
        "provider_search_on_scan": imports_cfg.provider_search_on_scan,
        "auto_pick_threshold": auto_pick_threshold,
    }

    show_items = await asyncio.to_thread(
        lambda: ScanFilesystemCollector(
            roots=show_roots,
            media_type=MediaType.show,
            **collector_kwargs,
        ).collect()
    )
    movie_items = await asyncio.to_thread(
        lambda: ScanFilesystemCollector(
            roots=movie_roots,
            media_type=MediaType.movie,
            **collector_kwargs,
        ).collect()
    )

    return ScanResponse(
        items=show_items + movie_items,
        ignored=sorted(ignored_paths),
    )

"""Library scan — walk configured roots and surface unimported directories.

This is the import domain's discovery flow: a pure, side-effect-free walk +
fuzzy-match + metadata-provider lookup that returns a ``ScanResponse``. The
side-effectful parts (auto-import of strong matches, caching) live in
``imports/tasks.py``. It was extracted out of ``TorrentService`` because a
filesystem scan is not a torrent concern — its only torrent-domain input was
the ignored-paths list, which callers now pass in.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from miramedia.config import MiraMediaConfig
from miramedia.imports.files import walk_importable_media_directories
from miramedia.imports.matching import (
    find_candidate_media_matches,
    score_title_match_with_breakdown,
)
from miramedia.imports.schemas import (
    ScanCandidate,
    ScanProviderCandidate,
    ScanResponse,
    ScanResult,
    ScanSourceFile,
)
from miramedia.naming import extract_external_id_from_string
from miramedia.torrents.parsing import is_video_file
from miramedia.torrents.schemas import MediaType

if TYPE_CHECKING:
    from miramedia.movies.service import MovieService
    from miramedia.shows.service import ShowService

log = logging.getLogger(__name__)


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
    while ``_collect`` runs.
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

    # Snapshot existing-media lists. Prefer caller-supplied services (cheap
    # — they're already attached to a session), but if absent open and
    # IMMEDIATELY close short bg sessions so the long walk that follows
    # holds nothing.
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
    # From here on the show/movie service refs are not used — the snapshot
    # lists are all we need for the walk + provider-search fan-out.
    del show_service
    del movie_service

    # Per-media expected canonical stems for files already imported.
    # ``_collect`` filters scanned files whose name starts with any of
    # these stems (plus ``"."``) so the canonical library folder stops
    # surfacing as Pending every scan. Stem-based matching (instead of
    # inode) avoids depending on the show/movie root path being
    # resolvable from config — which can drift when a show is filed
    # under a renamed library or its on-disk dir uses a fallback
    # naming-format variant.
    def _build_imported_stem_maps() -> tuple[dict, dict]:
        from miramedia.file_status import ImportOutcome
        from miramedia.naming import (
            episode_file_stem_candidates,
            movie_file_stem_candidates,
        )
        from miramedia.torrents.quality_naming import NameParts

        show_stems: dict = {}
        for show in existing_shows:
            stems: set[str] = set()
            for season in show.seasons:
                for episode in season.episodes:
                    for ef in episode.episode_files:
                        if ef.import_status != ImportOutcome.imported:
                            continue
                        stems.update(
                            episode_file_stem_candidates(
                                show,
                                season_number=season.number,
                                episode_number=episode.number,
                                quality=ef.quality,
                                parts=NameParts.from_row(ef),
                            )
                        )
            show_stems[show.id] = stems

        movie_stems: dict = {}
        for movie in existing_movies:
            stems = set()
            for mf in movie_files_by_id.get(movie.id, []):
                if mf.import_status != ImportOutcome.imported:
                    continue
                stems.update(
                    movie_file_stem_candidates(
                        movie, mf.quality, NameParts.from_row(mf)
                    )
                )
            movie_stems[movie.id] = stems

        return show_stems, movie_stems

    show_imported_stems, movie_imported_stems = await asyncio.to_thread(
        _build_imported_stem_maps
    )

    show_by_imdb = {s.imdb_id: s for s in existing_shows if getattr(s, "imdb_id", None)}
    movie_by_imdb = {
        m.imdb_id: m for m in existing_movies if getattr(m, "imdb_id", None)
    }
    show_by_external = {(s.external_id, s.metadata_provider): s for s in existing_shows}
    movie_by_external = {
        (m.external_id, m.metadata_provider): m for m in existing_movies
    }
    # Mirror ``build_folder_id_tag``'s tt-prefix fallback: shows added
    # via the native provider store the IMDb ID in ``external_id`` and
    # leave ``imdb_id`` null, but their folder still gets tagged
    # ``[imdb-tt...]``. Match those dirs back to the row by external_id
    # too so the scan doesn't keep surfacing them as Pending.
    show_by_tt_external = {
        s.external_id: s
        for s in existing_shows
        if isinstance(getattr(s, "external_id", None), str)
        and s.external_id.startswith("tt")
    }
    movie_by_tt_external = {
        m.external_id: m
        for m in existing_movies
        if isinstance(getattr(m, "external_id", None), str)
        and m.external_id.startswith("tt")
    }

    items: list[ScanResult] = []

    def _resolve_existing(media_type: MediaType, dir_name: str):  # noqa: ANN202
        """Return the tracked Show/Movie whose ``[provider-id]`` tag matches
        ``dir_name``, or ``None``. Used so a dir already named for a tracked
        item still gets surfaced (and auto-imported) — previously these
        dirs were silently skipped, so files dropped into a properly-named
        show/movie folder never made it into the library."""
        provider, ext_id = extract_external_id_from_string(dir_name)
        if not provider or not ext_id:
            return None
        if media_type == MediaType.show:
            hit = show_by_imdb.get(ext_id) or show_by_external.get((ext_id, provider))
            if hit is None and provider == "imdb":
                hit = show_by_tt_external.get(ext_id)
            return hit
        hit = movie_by_imdb.get(ext_id) or movie_by_external.get((ext_id, provider))
        if hit is None and provider == "imdb":
            hit = movie_by_tt_external.get(ext_id)
        return hit

    def _detect_name_year(dir_name: str) -> tuple[str, int | None]:
        m = re.match(r"^(.+?)\s*\((\d{4})\)", dir_name)
        if m:
            return m.group(1).strip(), int(m.group(2))
        stripped = re.sub(r"\s*\[[^\]]+\]\s*$", "", dir_name).strip()
        return stripped, None

    def _provider_search(
        media_type: MediaType, name: str, year: int | None
    ) -> list[ScanProviderCandidate]:
        """Query every enabled metadata provider by detected name/year and
        return the union of scored candidates. Querying only the native
        provider misses titles that exist in TMDB/TVDB but not TVMaze/
        Cinemeta (the typical case for newer documentaries / specials)."""
        from miramedia.metadata.dependencies import (
            get_all_enabled_providers,
        )

        providers = get_all_enabled_providers()
        if not providers:
            return []

        # Key on IMDb ID where available, otherwise (provider, provider_id)
        # so we don't keep duplicates when TMDB + native both return the
        # same title.
        by_key: dict[tuple[str, str], ScanProviderCandidate] = {}
        for provider in providers:
            try:
                if media_type == MediaType.show:
                    results = provider.search_show(query=name)
                else:
                    results = provider.search_movie(query=name)
            except Exception:
                log.warning(
                    "Provider search failed (%s) for %r",
                    provider.__class__.__name__,
                    name,
                    exc_info=True,
                )
                continue
            for r in results:
                conf, breakdown = score_title_match_with_breakdown(
                    name, year, r.name, r.year
                )
                if conf <= 0.3:
                    continue
                key = (
                    ("imdb", r.imdb_id)
                    if r.imdb_id
                    else (r.metadata_provider, str(r.external_id))
                )
                existing = by_key.get(key)
                if existing is not None and existing.confidence >= conf:
                    continue
                by_key[key] = ScanProviderCandidate(
                    media_type=media_type,
                    external_id=r.external_id,
                    metadata_provider=r.metadata_provider,
                    name=r.name,
                    year=r.year,
                    overview=r.overview,
                    poster_path=r.poster_path,
                    imdb_id=r.imdb_id,
                    confidence=conf,
                    breakdown=breakdown,
                )

        scored = sorted(by_key.values(), key=lambda c: c.confidence, reverse=True)
        return scored[: imports_cfg.provider_search_max_results]

    def _collect(roots: list[tuple[Path, str]], media_type: MediaType) -> None:
        root_paths = [r for r, _ in roots]
        label_by_path = {str(r.absolute()): name for r, name in roots}
        for media_dir in walk_importable_media_directories(
            root_paths, ignored_paths=ignored_paths
        ):
            if str(media_dir.absolute()) in ignored_abs:
                continue
            existing_match = _resolve_existing(media_type, media_dir.name)

            # Library is whichever root is an ancestor.
            library_name = "Default"
            for root_str, name in label_by_path.items():
                if (
                    str(media_dir.absolute()).startswith(root_str + "/")
                    or str(media_dir.absolute()) == root_str
                ):
                    library_name = name
                    break

            detected_name, detected_year = _detect_name_year(media_dir.name)

            # When the dir name resolves to a tracked show/movie, drop
            # files whose name matches the canonical stem of an already-
            # imported EpisodeFile / MovieFile row. Without this, the
            # library folder of any tracked media shows up as a Pending
            # import every scan (its videos ARE the library). Manual
            # copies / new qualities the user dropped in are still
            # surfaced because their filename won't match a stored
            # stem.
            imported_stems: set[str] = set()
            if existing_match is not None:
                imported_stems = (
                    show_imported_stems.get(existing_match.id, set())
                    if media_type == MediaType.show
                    else movie_imported_stems.get(existing_match.id, set())
                )

            def _is_already_imported(
                name: str, _imported_stems: set[str] = imported_stems
            ) -> bool:
                # Filename starts with a known stem + "." → it's a file
                # produced by an earlier import of this media (the
                # ``.mkv`` payload itself or sidecars like ``.en.srt``,
                # ``.nfo``).
                return any(name.startswith(stem + ".") for stem in _imported_stems)

            size_bytes = 0
            file_count = 0
            scan_files: list[ScanSourceFile] = []
            try:
                for sub in media_dir.rglob("*"):
                    if not sub.is_file():
                        continue
                    if imported_stems and _is_already_imported(sub.name):
                        continue
                    try:
                        fsize = sub.stat().st_size
                    except OSError:
                        fsize = 0
                    file_count += 1
                    size_bytes += fsize
                    try:
                        rel = str(sub.relative_to(media_dir))
                    except ValueError:
                        rel = sub.name
                    scan_files.append(
                        ScanSourceFile(
                            relative_path=rel,
                            size=fsize,
                            is_video=is_video_file(sub),
                        )
                    )
            except OSError:
                pass
            scan_files.sort(key=lambda f: f.relative_path)

            # If filtering left no video files behind, the dir is fully
            # already-imported; drop it from the scan response entirely.
            if existing_match is not None and not any(f.is_video for f in scan_files):
                continue

            raw_candidates = find_candidate_media_matches(
                media_dir.name,
                existing_shows if media_type == MediaType.show else [],
                existing_movies if media_type == MediaType.movie else [],
            )
            candidates = [
                ScanCandidate(
                    media_type=MediaType(c["media_type"]),
                    media_id=c["media_id"],
                    media_name=c["media_name"],
                    media_year=c["media_year"],
                    confidence=c["confidence"],
                    breakdown=c.get("breakdown"),
                )
                for c in raw_candidates[:5]
            ]

            # ``[provider-id]`` tag in the dir name is a hard identity
            # match — promote it to a 1.0 candidate so auto-import links
            # any unimported files inside an already-tracked media dir.
            if existing_match is not None:
                matched_id = existing_match.id
                candidates = [
                    ScanCandidate(
                        media_type=media_type,
                        media_id=matched_id,
                        media_name=existing_match.name,
                        media_year=getattr(existing_match, "year", None),
                        confidence=1.0,
                    ),
                    *[c for c in candidates if c.media_id != matched_id],
                ]

            top_existing = candidates[0].confidence if candidates else 0.0

            # Provider search: only when there's no strong existing match,
            # so we don't spend network calls on dirs that already map
            # cleanly to a tracked show/movie.
            provider_candidates: list[ScanProviderCandidate] = []
            if (
                imports_cfg.provider_search_on_scan
                and top_existing < auto_pick_threshold
            ):
                provider_candidates = _provider_search(
                    media_type, detected_name, detected_year
                )

            # NOTE: auto-import (create + import the best match without
            # human review) is intentionally NOT done here. scan_libraries
            # stays a pure walk+match+provider-search function; the
            # side-effectful auto-import is performed by the scan task.

            items.append(
                ScanResult(
                    directory=str(media_dir),
                    detected_name=detected_name,
                    detected_year=detected_year,
                    media_type_hint=media_type,
                    library_name=library_name,
                    size_bytes=size_bytes,
                    file_count=file_count,
                    candidates=candidates,
                    provider_candidates=provider_candidates,
                    files=scan_files,
                )
            )

    await asyncio.to_thread(_collect, show_roots, MediaType.show)
    await asyncio.to_thread(_collect, movie_roots, MediaType.movie)

    return ScanResponse(items=items, ignored=sorted(ignored_paths))

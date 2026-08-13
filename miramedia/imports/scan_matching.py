"""Pure library-scan matching, canonicalization, and candidate assembly.

Extracted from ``scan.py`` so matching phases are importable and directly
testable without walking the filesystem or holding DB sessions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from miramedia.imports.files import walk_importable_media_directories
from miramedia.imports.matching import (
    find_candidate_media_matches,
    score_title_match_with_breakdown,
)
from miramedia.imports.schemas import (
    MatchBreakdown,
    ScanCandidate,
    ScanProviderCandidate,
    ScanResult,
    ScanSourceFile,
)
from miramedia.naming import extract_external_id_from_string
from miramedia.torrents.parsing import is_video_file
from miramedia.torrents.schemas import MediaType

if TYPE_CHECKING:
    from miramedia.metadata.backends.generic import AbstractMetadataProvider
    from miramedia.movies.schemas import Movie
    from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExistingMediaIndexes:
    """Lookup tables for resolving ``[provider-id]`` tags to tracked media."""

    show_by_imdb: dict[str, Show]
    movie_by_imdb: dict[str, Movie]
    show_by_external: dict[tuple[str, str], Show]
    movie_by_external: dict[tuple[str, str], Movie]
    show_by_tt_external: dict[str, Show]
    movie_by_tt_external: dict[str, Movie]


def build_existing_media_indexes(
    existing_shows: list[Show],
    existing_movies: list[Movie],
) -> ExistingMediaIndexes:
    show_by_imdb = {s.imdb_id: s for s in existing_shows if s.imdb_id is not None}
    movie_by_imdb = {m.imdb_id: m for m in existing_movies if m.imdb_id is not None}
    show_by_external = {(s.external_id, s.metadata_provider): s for s in existing_shows}
    movie_by_external = {
        (m.external_id, m.metadata_provider): m for m in existing_movies
    }
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
    return ExistingMediaIndexes(
        show_by_imdb=show_by_imdb,
        movie_by_imdb=movie_by_imdb,
        show_by_external=show_by_external,
        movie_by_external=movie_by_external,
        show_by_tt_external=show_by_tt_external,
        movie_by_tt_external=movie_by_tt_external,
    )


def detect_name_year(dir_name: str) -> tuple[str, int | None]:
    m = re.match(r"^(.+?)\s*\((\d{4})\)", dir_name)
    if m:
        return m.group(1).strip(), int(m.group(2))
    stripped = re.sub(r"\s*\[[^\]]+\]\s*$", "", dir_name).strip()
    return stripped, None


def resolve_existing_media(
    media_type: MediaType,
    dir_name: str,
    indexes: ExistingMediaIndexes,
) -> Show | Movie | None:
    """Return tracked Show/Movie whose ``[provider-id]`` tag matches ``dir_name``."""
    provider, ext_id = extract_external_id_from_string(dir_name)
    if not provider or not ext_id:
        return None
    if media_type == MediaType.show:
        hit = indexes.show_by_imdb.get(ext_id) or indexes.show_by_external.get(
            (ext_id, provider)
        )
        if hit is None and provider == "imdb":
            hit = indexes.show_by_tt_external.get(ext_id)
        return hit
    hit = indexes.movie_by_imdb.get(ext_id) or indexes.movie_by_external.get(
        (ext_id, provider)
    )
    if hit is None and provider == "imdb":
        hit = indexes.movie_by_tt_external.get(ext_id)
    return hit


def build_imported_stem_maps(
    existing_shows: list[Show],
    existing_movies: list[Movie],
    movie_files_by_id: dict[Any, list],
) -> tuple[dict, dict]:
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
                movie_file_stem_candidates(movie, mf.quality, NameParts.from_row(mf))
            )
        movie_stems[movie.id] = stems

    return show_stems, movie_stems


def is_already_imported_file(name: str, imported_stems: set[str]) -> bool:
    return any(name.startswith(stem + ".") for stem in imported_stems)


def assemble_scan_candidates(
    media_dir_name: str,
    media_type: MediaType,
    existing_shows: list[Show],
    existing_movies: list[Movie],
    existing_match: Show | Movie | None,
) -> list[ScanCandidate]:
    raw_candidates = find_candidate_media_matches(
        media_dir_name,
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

    return candidates


class ProviderSearchCollaborator:
    """Fan-out metadata-provider search for unscanned directory names."""

    def __init__(
        self,
        providers: list[AbstractMetadataProvider],
        max_results: int,
    ) -> None:
        self._providers = providers
        self._max_results = max_results

    def search(
        self,
        media_type: MediaType,
        name: str,
        year: int | None,
    ) -> list[ScanProviderCandidate]:
        if not self._providers:
            return []

        by_key: dict[tuple[str, str], ScanProviderCandidate] = {}
        for provider in self._providers:
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
                    breakdown=MatchBreakdown.model_validate(breakdown),
                )

        scored = sorted(by_key.values(), key=lambda c: c.confidence, reverse=True)
        return scored[: self._max_results]


class ScanFilesystemCollector:
    """Walk library roots and build ``ScanResult`` rows from filesystem snapshots."""

    def __init__(
        self,
        *,
        roots: list[tuple[Path, str]],
        media_type: MediaType,
        ignored_paths: set[str],
        ignored_abs: set[str],
        existing_shows: list[Show],
        existing_movies: list[Movie],
        indexes: ExistingMediaIndexes,
        show_imported_stems: dict,
        movie_imported_stems: dict,
        provider_search: ProviderSearchCollaborator,
        provider_search_on_scan: bool,
        auto_pick_threshold: float,
    ) -> None:
        self._roots = roots
        self._media_type = media_type
        self._ignored_paths = ignored_paths
        self._ignored_abs = ignored_abs
        self._existing_shows = existing_shows
        self._existing_movies = existing_movies
        self._indexes = indexes
        self._show_imported_stems = show_imported_stems
        self._movie_imported_stems = movie_imported_stems
        self._provider_search = provider_search
        self._provider_search_on_scan = provider_search_on_scan
        self._auto_pick_threshold = auto_pick_threshold

    def collect(self) -> list[ScanResult]:
        items: list[ScanResult] = []
        root_paths = [r for r, _ in self._roots]
        label_by_path = {str(r.absolute()): name for r, name in self._roots}
        for media_dir in walk_importable_media_directories(
            root_paths, ignored_paths=self._ignored_paths
        ):
            if str(media_dir.absolute()) in self._ignored_abs:
                continue
            existing_match = resolve_existing_media(
                self._media_type, media_dir.name, self._indexes
            )

            library_name = "Default"
            for root_str, name in label_by_path.items():
                if (
                    str(media_dir.absolute()).startswith(root_str + "/")
                    or str(media_dir.absolute()) == root_str
                ):
                    library_name = name
                    break

            detected_name, detected_year = detect_name_year(media_dir.name)

            imported_stems: set[str] = set()
            if existing_match is not None:
                imported_stems = (
                    self._show_imported_stems.get(existing_match.id, set())
                    if self._media_type == MediaType.show
                    else self._movie_imported_stems.get(existing_match.id, set())
                )

            size_bytes = 0
            file_count = 0
            scan_files: list[ScanSourceFile] = []
            try:
                for sub in media_dir.rglob("*"):
                    if not sub.is_file():
                        continue
                    if imported_stems and is_already_imported_file(
                        sub.name, imported_stems
                    ):
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

            if existing_match is not None and not any(f.is_video for f in scan_files):
                continue

            candidates = assemble_scan_candidates(
                media_dir.name,
                self._media_type,
                self._existing_shows,
                self._existing_movies,
                existing_match,
            )

            top_existing = candidates[0].confidence if candidates else 0.0

            provider_candidates: list[ScanProviderCandidate] = []
            if (
                self._provider_search_on_scan
                and top_existing < self._auto_pick_threshold
            ):
                provider_candidates = self._provider_search.search(
                    self._media_type, detected_name, detected_year
                )

            items.append(
                ScanResult(
                    directory=str(media_dir),
                    detected_name=detected_name,
                    detected_year=detected_year,
                    media_type_hint=self._media_type,
                    library_name=library_name,
                    size_bytes=size_bytes,
                    file_count=file_count,
                    candidates=candidates,
                    provider_candidates=provider_candidates,
                    files=scan_files,
                )
            )
        return items

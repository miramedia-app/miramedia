"""Bind feed envelopes to library rows (design 385 §3.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from miramedia.feeds.schemas import FeedEnvelope
from miramedia.indexers.utils import (
    _is_title_relevant,
    _is_year_relevant,
    _looks_like_episode,
    _normalized_name_variants,
    sanitize_search_query,
)
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show


@dataclass(frozen=True)
class FeedBindResult:
    media_type: Literal["movie", "show"] | None
    media_id: UUID | None
    movie: Movie | None = None
    show: Show | None = None


@dataclass(frozen=True)
class _CatalogMovie:
    movie: Movie
    norm_variants: tuple[str, ...]


@dataclass(frozen=True)
class _CatalogShow:
    show: Show
    norm_variants: tuple[str, ...]


@dataclass(frozen=True)
class FeedBindCatalog:
    """Poll-local immutable lookup built once per feed poll."""

    global_continuous_download: bool
    imdb_movies: dict[str, tuple[_CatalogMovie, ...]]
    imdb_shows: dict[str, tuple[_CatalogShow, ...]]
    tmdb_movies: dict[str, tuple[_CatalogMovie, ...]]
    tmdb_shows: dict[str, tuple[_CatalogShow, ...]]
    tvdb_shows: dict[str, tuple[_CatalogShow, ...]]
    show_title_index: dict[str, tuple[_CatalogShow, ...]]
    movie_title_index: dict[str, tuple[_CatalogMovie, ...]]
    eligible_movies: tuple[_CatalogMovie, ...]
    eligible_shows: tuple[_CatalogShow, ...]


def _continuous_download_enabled(
    skipped: bool, continuous_download: bool | None, global_default: bool
) -> bool:
    if skipped:
        return False
    if continuous_download is False:
        return False
    if continuous_download is True:
        return True
    return global_default


def _normalize_imdb_id(imdb_id: str) -> str:
    if not imdb_id.startswith("tt"):
        return f"tt{imdb_id}"
    return imdb_id


def _append_index_entry[T](index: dict[str, tuple[T, ...]], key: str, entry: T) -> None:
    if key in index:
        index[key] = index[key] + (entry,)
    else:
        index[key] = (entry,)


def _title_prefix_keys(norm_title: str) -> list[str]:
    tokens = norm_title.split()
    if not tokens:
        return []
    return [" ".join(tokens[: index + 1]) for index in range(len(tokens))]


def _unique_catalog_movies(entries: list[_CatalogMovie]) -> tuple[_CatalogMovie, ...]:
    seen: set[UUID] = set()
    out: list[_CatalogMovie] = []
    for entry in entries:
        if entry.movie.id in seen:
            continue
        seen.add(entry.movie.id)
        out.append(entry)
    return tuple(out)


def _unique_catalog_shows(entries: list[_CatalogShow]) -> tuple[_CatalogShow, ...]:
    seen: set[UUID] = set()
    out: list[_CatalogShow] = []
    for entry in entries:
        if entry.show.id in seen:
            continue
        seen.add(entry.show.id)
        out.append(entry)
    return tuple(out)


def build_feed_catalog(
    *,
    movies: list[Movie],
    shows: list[Show],
    global_continuous_download: bool,
) -> FeedBindCatalog:
    """Build a poll-local catalog; normalization runs once per media row."""
    imdb_movies: dict[str, tuple[_CatalogMovie, ...]] = {}
    imdb_shows: dict[str, tuple[_CatalogShow, ...]] = {}
    tmdb_movies: dict[str, tuple[_CatalogMovie, ...]] = {}
    tmdb_shows: dict[str, tuple[_CatalogShow, ...]] = {}
    tvdb_shows: dict[str, tuple[_CatalogShow, ...]] = {}
    show_title_index: dict[str, tuple[_CatalogShow, ...]] = {}
    movie_title_index: dict[str, tuple[_CatalogMovie, ...]] = {}
    eligible_movies: list[_CatalogMovie] = []
    eligible_shows: list[_CatalogShow] = []

    for movie in movies:
        if not _continuous_download_enabled(
            movie.skipped, movie.continuous_download, global_continuous_download
        ):
            continue
        entry = _CatalogMovie(movie, tuple(_normalized_name_variants(movie.name)))
        eligible_movies.append(entry)
        if movie.imdb_id:
            _append_index_entry(imdb_movies, _normalize_imdb_id(movie.imdb_id), entry)
        if movie.external_id:
            _append_index_entry(imdb_movies, movie.external_id, entry)
        if movie.metadata_provider == "tmdb" and movie.external_id:
            _append_index_entry(tmdb_movies, movie.external_id, entry)
        for variant in entry.norm_variants:
            _append_index_entry(movie_title_index, variant, entry)

    for show in shows:
        if not _continuous_download_enabled(
            show.skipped, show.continuous_download, global_continuous_download
        ):
            continue
        entry = _CatalogShow(show, tuple(_normalized_name_variants(show.name)))
        eligible_shows.append(entry)
        if show.imdb_id:
            _append_index_entry(imdb_shows, _normalize_imdb_id(show.imdb_id), entry)
        if show.external_id:
            _append_index_entry(imdb_shows, show.external_id, entry)
        if show.metadata_provider == "tmdb" and show.external_id:
            _append_index_entry(tmdb_shows, show.external_id, entry)
        if show.metadata_provider == "tvdb" and show.external_id:
            _append_index_entry(tvdb_shows, show.external_id, entry)
        for variant in entry.norm_variants:
            _append_index_entry(show_title_index, variant, entry)

    return FeedBindCatalog(
        global_continuous_download=global_continuous_download,
        imdb_movies=imdb_movies,
        imdb_shows=imdb_shows,
        tmdb_movies=tmdb_movies,
        tmdb_shows=tmdb_shows,
        tvdb_shows=tvdb_shows,
        show_title_index=show_title_index,
        movie_title_index=movie_title_index,
        eligible_movies=tuple(eligible_movies),
        eligible_shows=tuple(eligible_shows),
    )


def _bind_from_unique_movie(entry: _CatalogMovie) -> FeedBindResult:
    return FeedBindResult("movie", entry.movie.id, movie=entry.movie)


def _bind_from_unique_show(entry: _CatalogShow) -> FeedBindResult:
    return FeedBindResult("show", entry.show.id, show=entry.show)


def _bind_from_movie_bucket(
    bucket: tuple[_CatalogMovie, ...],
) -> FeedBindResult:
    unique = _unique_catalog_movies(list(bucket))
    if len(unique) == 1:
        return _bind_from_unique_movie(unique[0])
    return FeedBindResult(None, None)


def _bind_from_show_bucket(bucket: tuple[_CatalogShow, ...]) -> FeedBindResult:
    unique = _unique_catalog_shows(list(bucket))
    if len(unique) == 1:
        return _bind_from_unique_show(unique[0])
    return FeedBindResult(None, None)


def _title_show_candidates(
    catalog: FeedBindCatalog, title: str
) -> tuple[_CatalogShow, ...]:
    norm_title = sanitize_search_query(title).lower()
    collected: list[_CatalogShow] = []
    for prefix in _title_prefix_keys(norm_title):
        collected.extend(catalog.show_title_index.get(prefix, ()))
    return _unique_catalog_shows(collected)


def _title_movie_candidates(
    catalog: FeedBindCatalog, title: str
) -> tuple[_CatalogMovie, ...]:
    norm_title = sanitize_search_query(title).lower()
    collected: list[_CatalogMovie] = []
    for prefix in _title_prefix_keys(norm_title):
        collected.extend(catalog.movie_title_index.get(prefix, ()))
    return _unique_catalog_movies(collected)


def bind_feed_envelope_indexed(
    envelope: FeedEnvelope,
    catalog: FeedBindCatalog,
) -> FeedBindResult:
    """First-hit bind order from design §3.2, using a poll-local catalog."""
    result = envelope.result

    if envelope.imdb_id:
        imdb = _normalize_imdb_id(envelope.imdb_id)
        movie_bucket = catalog.imdb_movies.get(imdb, ())
        if movie_bucket:
            bind = _bind_from_movie_bucket(movie_bucket)
            if bind.media_type is not None:
                return bind
            if len(_unique_catalog_movies(list(movie_bucket))) > 1:
                return FeedBindResult(None, None)
        show_bucket = catalog.imdb_shows.get(imdb, ())
        if show_bucket:
            return _bind_from_show_bucket(show_bucket)

    if envelope.tmdb_id:
        movie_bucket = catalog.tmdb_movies.get(envelope.tmdb_id, ())
        if movie_bucket:
            bind = _bind_from_movie_bucket(movie_bucket)
            if bind.media_type is not None:
                return bind
            if len(_unique_catalog_movies(list(movie_bucket))) > 1:
                return FeedBindResult(None, None)
        show_bucket = catalog.tmdb_shows.get(envelope.tmdb_id, ())
        if show_bucket:
            return _bind_from_show_bucket(show_bucket)

    if envelope.tvdb_id:
        show_bucket = catalog.tvdb_shows.get(envelope.tvdb_id, ())
        if show_bucket:
            return _bind_from_show_bucket(show_bucket)

    looks_tv = _looks_like_episode(result.title) or bool(result.season)

    if looks_tv and result.season:
        candidates = [
            entry.show
            for entry in _title_show_candidates(catalog, result.title)
            if _is_title_relevant(
                result.title, list(entry.norm_variants), entry.show.year
            )
        ]
        if len(candidates) == 1:
            show = candidates[0]
            return FeedBindResult("show", show.id, show=show)
        if len(candidates) > 1:
            return FeedBindResult(None, None)

    if not looks_tv:
        candidates = [
            entry.movie
            for entry in _title_movie_candidates(catalog, result.title)
            if _is_title_relevant(
                result.title, list(entry.norm_variants), entry.movie.year
            )
            and _is_year_relevant(result.title, entry.movie.year)
        ]
        if len(candidates) == 1:
            movie = candidates[0]
            return FeedBindResult("movie", movie.id, movie=movie)
        if len(candidates) > 1:
            return FeedBindResult(None, None)

    return FeedBindResult(None, None)


def bind_feed_envelope(
    envelope: FeedEnvelope,
    *,
    movies: list[Movie],
    shows: list[Show],
    global_continuous_download: bool,
) -> FeedBindResult:
    """Compatibility wrapper that builds a catalog for a single envelope."""
    catalog = build_feed_catalog(
        movies=movies,
        shows=shows,
        global_continuous_download=global_continuous_download,
    )
    return bind_feed_envelope_indexed(envelope, catalog)

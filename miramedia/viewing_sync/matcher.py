"""Identity matching for external viewing-state import (design 386 §3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from miramedia.viewing_sync.schemas import (
    ExternalViewingEvent,
    MatchConfidence,
    MediaKind,
    MediaMatchResult,
    QuarantineReason,
    QuarantineRecord,
)


class MovieLike(Protocol):
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


class ShowLike(Protocol):
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


class SeasonLike(Protocol):
    id: UUID
    number: int
    show_id: UUID


class EpisodeLike(Protocol):
    id: UUID
    season_id: UUID
    number: int


@dataclass(frozen=True, slots=True)
class MovieCatalogLookup:
    by_imdb: dict[str, tuple[UUID, ...]]
    by_tmdb: dict[str, tuple[UUID, ...]]
    by_tvdb: dict[str, tuple[UUID, ...]]


@dataclass(frozen=True, slots=True)
class ShowCatalogLookup:
    by_imdb: dict[str, tuple[UUID, ...]]
    by_tmdb: dict[str, tuple[UUID, ...]]
    by_tvdb: dict[str, tuple[UUID, ...]]


@dataclass(frozen=True, slots=True)
class EpisodeCatalog:
    episodes_by_show_season_episode: dict[tuple[UUID, int, int], tuple[UUID, ...]]


@dataclass(frozen=True, slots=True)
class MediaCatalog:
    movies: MovieCatalogLookup
    shows: ShowCatalogLookup
    episodes: EpisodeCatalog


def normalize_imdb_id(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped if stripped.startswith("tt") else f"tt{stripped}"


def normalize_provider_ids(raw: dict[str, str] | None) -> dict[str, str]:
    if not raw:
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized[key.lower()] = text
    return normalized


def _provider_id(provider_ids: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = provider_ids.get(key.lower())
        if value:
            return value
    return None


def _append_unique(bucket: dict[str, list[UUID]], key: str, media_id: UUID) -> None:
    ids = bucket.setdefault(key, [])
    if media_id not in ids:
        ids.append(media_id)


def _build_imdb_index(
    rows: Sequence[MovieLike | ShowLike],
) -> dict[str, tuple[UUID, ...]]:
    by_imdb: dict[str, list[UUID]] = {}
    for row in rows:
        if row.imdb_id:
            imdb = normalize_imdb_id(row.imdb_id)
            if imdb is not None:
                _append_unique(by_imdb, imdb, row.id)
        if row.metadata_provider == "native":
            imdb = normalize_imdb_id(row.external_id)
            if imdb is not None:
                _append_unique(by_imdb, imdb, row.id)
    return {key: tuple(ids) for key, ids in by_imdb.items()}


def _build_provider_index(
    rows: Sequence[MovieLike | ShowLike],
    provider: str,
) -> dict[str, tuple[UUID, ...]]:
    by_provider: dict[str, list[UUID]] = {}
    for row in rows:
        if row.metadata_provider == provider:
            _append_unique(by_provider, row.external_id, row.id)
    return {key: tuple(ids) for key, ids in by_provider.items()}


def build_movie_catalog_lookup(movies: Sequence[MovieLike]) -> MovieCatalogLookup:
    return MovieCatalogLookup(
        by_imdb=_build_imdb_index(movies),
        by_tmdb=_build_provider_index(movies, "tmdb"),
        by_tvdb=_build_provider_index(movies, "tvdb"),
    )


def build_show_catalog_lookup(shows: Sequence[ShowLike]) -> ShowCatalogLookup:
    return ShowCatalogLookup(
        by_imdb=_build_imdb_index(shows),
        by_tmdb=_build_provider_index(shows, "tmdb"),
        by_tvdb=_build_provider_index(shows, "tvdb"),
    )


def build_episode_catalog(
    *,
    seasons: Sequence[SeasonLike],
    episodes: Sequence[EpisodeLike],
) -> EpisodeCatalog:
    season_id_to_show = {season.id: season.show_id for season in seasons}
    season_number_by_id = {season.id: season.number for season in seasons}
    episodes_by_coord: dict[tuple[UUID, int, int], list[UUID]] = {}
    for episode in episodes:
        show_id = season_id_to_show.get(episode.season_id)
        season_number = season_number_by_id.get(episode.season_id)
        if show_id is None or season_number is None:
            continue
        key = (show_id, season_number, episode.number)
        episodes_by_coord.setdefault(key, []).append(episode.id)
    return EpisodeCatalog(
        episodes_by_show_season_episode={
            key: tuple(ids) for key, ids in episodes_by_coord.items()
        }
    )


def build_media_catalog(
    *,
    movies: Sequence[MovieLike],
    shows: Sequence[ShowLike],
    seasons: Sequence[SeasonLike],
    episodes: Sequence[EpisodeLike],
) -> MediaCatalog:
    return MediaCatalog(
        movies=build_movie_catalog_lookup(movies),
        shows=build_show_catalog_lookup(shows),
        episodes=build_episode_catalog(seasons=seasons, episodes=episodes),
    )


def media_catalog_from_sequences(
    movies: Sequence[MovieLike],
    shows: Sequence[ShowLike],
    episodes_by_show_season: dict[tuple[UUID, int], Sequence[EpisodeLike]],
) -> MediaCatalog:
    episodes_by_coord: dict[tuple[UUID, int, int], list[UUID]] = {}
    for (show_id, season_number), episode_list in episodes_by_show_season.items():
        for episode in episode_list:
            key = (show_id, season_number, episode.number)
            episodes_by_coord.setdefault(key, []).append(episode.id)
    return MediaCatalog(
        movies=build_movie_catalog_lookup(movies),
        shows=build_show_catalog_lookup(shows),
        episodes=EpisodeCatalog(
            episodes_by_show_season_episode={
                key: tuple(ids) for key, ids in episodes_by_coord.items()
            }
        ),
    )


def _movie_match_from_hits(hits: Sequence[UUID]) -> MediaMatchResult:
    if not hits:
        return MediaMatchResult(confidence=MatchConfidence.unmatched)
    if len(hits) == 1:
        return MediaMatchResult(
            confidence=MatchConfidence.unique,
            media_kind=MediaKind.movie,
            media_id=hits[0],
            candidate_ids=(hits[0],),
        )
    return MediaMatchResult(
        confidence=MatchConfidence.ambiguous,
        media_kind=MediaKind.movie,
        candidate_ids=tuple(hits),
        reason="ambiguous_matches",
    )


def _show_match_from_hits(hits: Sequence[UUID]) -> MediaMatchResult:
    if not hits:
        return MediaMatchResult(confidence=MatchConfidence.unmatched)
    if len(hits) == 1:
        return MediaMatchResult(
            confidence=MatchConfidence.unique,
            media_kind=MediaKind.episode,
            media_id=hits[0],
            candidate_ids=(hits[0],),
        )
    return MediaMatchResult(
        confidence=MatchConfidence.ambiguous,
        candidate_ids=tuple(hits),
        reason="ambiguous_matches",
    )


def match_movie(
    catalog: MovieCatalogLookup,
    provider_ids: dict[str, str],
) -> MediaMatchResult:
    normalized = normalize_provider_ids(provider_ids)
    if not normalized:
        return MediaMatchResult(
            confidence=MatchConfidence.unmatched,
            reason="missing_provider_ids",
        )

    imdb = normalize_imdb_id(_provider_id(normalized, "imdb"))
    if imdb:
        hits = catalog.by_imdb.get(imdb, ())
        if len(hits) == 1:
            return MediaMatchResult(
                confidence=MatchConfidence.unique,
                media_kind=MediaKind.movie,
                media_id=hits[0],
                candidate_ids=(hits[0],),
            )
        if len(hits) > 1:
            return MediaMatchResult(
                confidence=MatchConfidence.ambiguous,
                media_kind=MediaKind.movie,
                candidate_ids=hits,
                reason="ambiguous_matches",
            )

    tmdb = _provider_id(normalized, "tmdb")
    if tmdb:
        hits = catalog.by_tmdb.get(tmdb, ())
        result = _movie_match_from_hits(hits)
        if result.confidence != MatchConfidence.unmatched:
            return result

    tvdb = _provider_id(normalized, "tvdb")
    if tvdb:
        hits = catalog.by_tvdb.get(tvdb, ())
        result = _movie_match_from_hits(hits)
        if result.confidence != MatchConfidence.unmatched:
            return result

    return MediaMatchResult(confidence=MatchConfidence.unmatched, reason="zero_matches")


def match_show(
    catalog: ShowCatalogLookup,
    provider_ids: dict[str, str],
) -> MediaMatchResult:
    normalized = normalize_provider_ids(provider_ids)
    if not normalized:
        return MediaMatchResult(
            confidence=MatchConfidence.unmatched,
            reason="missing_provider_ids",
        )

    imdb = normalize_imdb_id(_provider_id(normalized, "imdb"))
    if imdb:
        hits = catalog.by_imdb.get(imdb, ())
        if len(hits) == 1:
            return MediaMatchResult(
                confidence=MatchConfidence.unique,
                media_kind=MediaKind.episode,
                media_id=hits[0],
                candidate_ids=(hits[0],),
            )
        if len(hits) > 1:
            return MediaMatchResult(
                confidence=MatchConfidence.ambiguous,
                candidate_ids=hits,
                reason="ambiguous_matches",
            )

    tmdb = _provider_id(normalized, "tmdb")
    if tmdb:
        hits = catalog.by_tmdb.get(tmdb, ())
        result = _show_match_from_hits(hits)
        if result.confidence != MatchConfidence.unmatched:
            return result

    tvdb = _provider_id(normalized, "tvdb")
    if tvdb:
        hits = catalog.by_tvdb.get(tvdb, ())
        result = _show_match_from_hits(hits)
        if result.confidence != MatchConfidence.unmatched:
            return result

    return MediaMatchResult(confidence=MatchConfidence.unmatched, reason="zero_matches")


def match_episode(
    *,
    catalog: MediaCatalog,
    provider_ids: dict[str, str],
    season_number: int | None,
    episode_number: int | None,
    episode_number_end: int | None,
) -> MediaMatchResult:
    if (
        episode_number_end is not None
        and episode_number is not None
        and episode_number_end != episode_number
    ):
        return MediaMatchResult(
            confidence=MatchConfidence.unmatched,
            reason="multi_episode",
        )
    if season_number is None or episode_number is None:
        return MediaMatchResult(
            confidence=MatchConfidence.unmatched,
            reason="zero_matches",
        )

    show_match = match_show(catalog.shows, provider_ids)
    if show_match.confidence != MatchConfidence.unique or show_match.media_id is None:
        return show_match

    show_id = show_match.media_id
    hits = catalog.episodes.episodes_by_show_season_episode.get(
        (show_id, season_number, episode_number),
        (),
    )
    if len(hits) == 1:
        return MediaMatchResult(
            confidence=MatchConfidence.unique,
            media_kind=MediaKind.episode,
            media_id=hits[0],
            candidate_ids=(hits[0],),
        )
    if len(hits) > 1:
        return MediaMatchResult(
            confidence=MatchConfidence.ambiguous,
            media_kind=MediaKind.episode,
            candidate_ids=hits,
            reason="ambiguous_matches",
        )
    return MediaMatchResult(
        confidence=MatchConfidence.unmatched,
        media_kind=MediaKind.episode,
        reason="zero_matches",
    )


def match_event_media(
    event: ExternalViewingEvent,
    *,
    catalog: MediaCatalog,
) -> MediaMatchResult:
    if event.media_kind == MediaKind.movie:
        return match_movie(catalog.movies, event.provider_ids)
    return match_episode(
        catalog=catalog,
        provider_ids=event.provider_ids,
        season_number=event.season_number,
        episode_number=event.episode_number,
        episode_number_end=event.episode_number_end,
    )


def quarantine_from_match(
    event: ExternalViewingEvent,
    match: MediaMatchResult,
) -> QuarantineRecord | None:
    if match.confidence == MatchConfidence.unique:
        return None
    reason = QuarantineReason.zero_matches
    if match.reason == "missing_provider_ids":
        reason = QuarantineReason.missing_provider_ids
    elif match.reason == "ambiguous_matches":
        reason = QuarantineReason.ambiguous_matches
    elif match.reason == "multi_episode":
        reason = QuarantineReason.multi_episode
    return QuarantineRecord(
        reason=reason,
        connector_user_id=event.connector_user_id,
        connector_item_id=event.connector_item_id,
        item_type=event.media_kind.value,
        provider_ids=dict(event.provider_ids),
        candidate_mira_ids=match.candidate_ids,
        title=event.title,
        year=event.year,
        series_name=event.series_name,
        season=event.season_number,
        episode=event.episode_number,
    )

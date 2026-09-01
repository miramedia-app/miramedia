"""Dict-backed repository fakes with real in-memory state."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import Movie, MovieFile, MovieId
from miramedia.playback.schemas import MediaKind, PlaybackProgress, WatchState
from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestId,
    RequestSource,
    RequestStatus,
)
from miramedia.shows.schemas import (
    Episode,
    EpisodeAttributeChange,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.integrity import Sha1MismatchPage, Sha1MismatchPageKey
from miramedia.torrents.schemas import Quality, Torrent, TorrentId, TorrentStatus
from tests.fakes.db import FakeDb


class FakeShowRepository:
    def __init__(self) -> None:
        self.db = FakeDb()
        self.get_show_by_id_calls = 0
        self.shows: dict[ShowId, Show] = {}
        self.episodes: dict[EpisodeId, Episode] = {}
        self.seasons: dict[SeasonId, Season] = {}
        self.episode_files: dict[UUID, EpisodeFile] = {}
        self.torrents_by_show: dict[ShowId, list[Torrent]] = {}

    def add_show(self, show: Show) -> Show:
        self.shows[show.id] = show
        for season in show.seasons:
            self.seasons[season.id] = season
            for episode in season.episodes:
                self.episodes[episode.id] = episode
        return show

    async def get_show_by_id(self, *, show_id: ShowId) -> Show | None:
        self.get_show_by_id_calls += 1
        return self.shows.get(show_id)

    async def get_shows_by_ids(self, show_ids: list[ShowId]) -> dict[ShowId, Show]:
        self.get_shows_by_ids_calls = getattr(self, "get_shows_by_ids_calls", 0) + 1
        self.last_show_ids_batch = list(show_ids)
        return {
            show_id: self.shows[show_id]
            for show_id in show_ids
            if show_id in self.shows
        }

    async def get_season(self, *, season_id: SeasonId) -> Season:
        return self.seasons[season_id]

    async def get_seasons_by_ids(
        self, season_ids: list[SeasonId]
    ) -> dict[SeasonId, Season]:
        self.get_seasons_by_ids_calls = getattr(self, "get_seasons_by_ids_calls", 0) + 1
        self.last_season_ids_batch = list(season_ids)
        return {
            season_id: self.seasons[season_id]
            for season_id in season_ids
            if season_id in self.seasons
        }

    async def get_show_by_season_id(self, *, season_id: SeasonId) -> Show:
        season = self.seasons[season_id]
        return self.shows[season.show_id]

    async def update_season_skipped(
        self, *, season_id: SeasonId, skipped: bool
    ) -> None:
        season = self.seasons[season_id]
        self.seasons[season_id] = season.model_copy(update={"skipped": skipped})
        show = self.shows[season.show_id]
        updated_seasons = [
            self.seasons[season_id] if s.id == season_id else s for s in show.seasons
        ]
        self.shows[season.show_id] = show.model_copy(
            update={"seasons": updated_seasons}
        )

    async def update_episode_skipped(
        self, *, episode_id: EpisodeId, skipped: bool
    ) -> None:
        episode = self.episodes[episode_id]
        self.episodes[episode_id] = episode.model_copy(update={"skipped": skipped})
        season = _season_for_episode(self, episode_id)
        updated_episodes = [
            self.episodes[episode_id] if ep.id == episode_id else ep
            for ep in season.episodes
        ]
        updated_season = season.model_copy(update={"episodes": updated_episodes})
        self.seasons[season.id] = updated_season
        show = self.shows[season.show_id]
        updated_seasons = [
            updated_season if s.id == season.id else s for s in show.seasons
        ]
        self.shows[season.show_id] = show.model_copy(
            update={"seasons": updated_seasons}
        )

    async def update_episodes_skipped_bulk(
        self, episode_ids: list[EpisodeId], skipped: bool
    ) -> None:
        for episode_id in episode_ids:
            await self.update_episode_skipped(episode_id=episode_id, skipped=skipped)

    async def update_episodes_attributes_bulk(
        self, changes: Sequence[EpisodeAttributeChange]
    ) -> None:
        for change in changes:
            episode = self.episodes.get(change.episode_id)
            if episode is None:
                from miramedia.exceptions import NotFoundError

                msg = f"Episode with id {change.episode_id} not found."
                raise NotFoundError(msg)
            updates: dict[str, object] = {}
            if change.title is not None and episode.title != change.title:
                updates["title"] = change.title
            if change.overview is not None and episode.overview != change.overview:
                updates["overview"] = change.overview
            if change.air_date is not None and episode.air_date != change.air_date:
                updates["air_date"] = change.air_date
            if change.air_time is not None and episode.air_time != change.air_time:
                updates["air_time"] = change.air_time
            if not updates:
                continue
            self.episodes[change.episode_id] = episode.model_copy(update=updates)
            season = _season_for_episode(self, change.episode_id)
            updated_episodes = [
                self.episodes[change.episode_id] if ep.id == change.episode_id else ep
                for ep in season.episodes
            ]
            updated_season = season.model_copy(update={"episodes": updated_episodes})
            self.seasons[season.id] = updated_season
            show = self.shows[season.show_id]
            updated_seasons = [
                updated_season if s.id == season.id else s for s in show.seasons
            ]
            self.shows[season.show_id] = show.model_copy(
                update={"seasons": updated_seasons}
            )

    async def get_episode(self, *, episode_id: EpisodeId) -> Episode:
        return self.episodes[episode_id]

    async def get_season_by_episode(self, *, episode_id: EpisodeId) -> Season:
        for season in self.seasons.values():
            if any(ep.id == episode_id for ep in season.episodes):
                return season
        msg = f"season not found for episode {episode_id}"
        raise KeyError(msg)

    async def get_episodes_with_seasons(
        self, episode_ids: list[EpisodeId]
    ) -> dict[EpisodeId, tuple[Season, Episode]]:
        self.get_episodes_with_seasons_calls = (
            getattr(self, "get_episodes_with_seasons_calls", 0) + 1
        )
        self.last_episode_ids_batch = list(episode_ids)
        out: dict[EpisodeId, tuple[Season, Episode]] = {}
        for episode_id in episode_ids:
            episode = self.episodes.get(episode_id)
            if episode is None:
                continue
            season = _season_for_episode(self, episode_id)
            out[episode_id] = (season, episode)
        return out

    async def batch_episodes_with_context(
        self, episode_ids: list[EpisodeId]
    ) -> dict[EpisodeId, object]:
        from miramedia.shows.schemas import EpisodeIntegrityContext

        self.context_batch_calls = getattr(self, "context_batch_calls", 0) + 1
        out: dict[EpisodeId, object] = {}
        for episode_id in episode_ids:
            episode = self.episodes.get(episode_id)
            if episode is None:
                continue
            season = next(
                (
                    s
                    for s in self.seasons.values()
                    if any(ep.id == episode_id for ep in s.episodes)
                ),
                None,
            )
            if season is None:
                continue
            show = self.shows.get(season.show_id)
            if show is None:
                continue
            out[episode_id] = EpisodeIntegrityContext(
                episode_number=int(episode.number),
                season_number=int(season.number),
                show_id=season.show_id,
                show_name=show.name,
            )
        return out

    async def get_episode_files_by_season_id(
        self, *, season_id: SeasonId
    ) -> list[EpisodeFile]:
        return [
            f
            for f in self.episode_files.values()
            if _season_for_episode(self, f.episode_id).id == season_id
        ]

    async def get_episode_files_by_season_ids(
        self, season_ids: list[SeasonId]
    ) -> dict[SeasonId, list[EpisodeFile]]:
        self.get_episode_files_by_season_ids_calls = (
            getattr(self, "get_episode_files_by_season_ids_calls", 0) + 1
        )
        season_id_set = set(season_ids)
        grouped: dict[SeasonId, list[EpisodeFile]] = {
            season_id: [] for season_id in season_ids
        }
        for episode_file in self.episode_files.values():
            season = _season_for_episode(self, episode_file.episode_id)
            if season.id in season_id_set:
                grouped[season.id].append(episode_file)
        return grouped

    async def get_episode_files_by_episode_id(
        self, episode_id: EpisodeId
    ) -> list[EpisodeFile]:
        return [
            episode_file
            for episode_file in self.episode_files.values()
            if episode_file.episode_id == episode_id
        ]

    async def get_torrents_by_show_id(self, *, show_id: ShowId) -> list[Torrent]:
        return list(self.torrents_by_show.get(show_id, []))

    async def set_auto_download_backoff(
        self, show_id: ShowId, until: datetime | None
    ) -> None:
        show = self.shows.get(show_id)
        if show is not None:
            self.shows[show_id] = show.model_copy(
                update={"auto_download_backoff_until": until}
            )

    async def get_orphaned_failed_episode_files(self) -> list[EpisodeFile]:
        return [
            f
            for f in self.episode_files.values()
            if f.torrent_id is None
            and f.import_status
            in (ImportOutcome.failed_io, ImportOutcome.failed_no_match)
        ]

    async def update_episode_file_import_status(
        self,
        *,
        file_id: UUID,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        await self.update_episode_file_import_status_bulk(
            file_ids=[file_id],
            status=status,
            error=error,
        )

    async def update_episode_file_import_status_bulk(
        self,
        *,
        file_ids: list[UUID],
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        for file_id in file_ids:
            row = self.episode_files[file_id]
            self.episode_files[file_id] = row.model_copy(
                update={
                    "import_status": status,
                    "import_error": error,
                    "last_attempt_at": now,
                    "attempt_count": row.attempt_count + 1,
                    "imported_at": now
                    if status == ImportOutcome.imported
                    else row.imported_at,
                }
            )

    async def finalize_episode_file_import(
        self,
        *,
        file_id: UUID,
        quality: Quality,
        codec: str,
        hdr: bool,
        source: str,
        variant: str,
        extra: str,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        row = self.episode_files[file_id]
        now = datetime.now(UTC)
        self.episode_files[file_id] = row.model_copy(
            update={
                "quality": quality,
                "codec": codec,
                "hdr": hdr,
                "source": source,
                "variant": variant,
                "extra": extra,
                "import_status": status,
                "import_error": error,
                "last_attempt_at": now,
                "attempt_count": row.attempt_count + 1,
                "imported_at": now
                if status == ImportOutcome.imported
                else row.imported_at,
            }
        )

    async def add_episode_files(
        self, episode_files: list[EpisodeFile]
    ) -> list[EpisodeFile]:
        added: list[EpisodeFile] = []
        for episode_file in episode_files:
            self.episode_files[episode_file.id] = episode_file
            episode = self.episodes[episode_file.episode_id]
            episode.episode_files = [*episode.episode_files, episode_file]
            self.episodes[episode_file.episode_id] = episode
            added.append(episode_file)
        return added

    async def add_episode_file(self, *, episode_file: EpisodeFile) -> EpisodeFile:
        return (await self.add_episode_files([episode_file]))[0]

    async def add_episodes_to_season(
        self,
        season_id: SeasonId,
        episodes: list[Episode],
        *,
        skipped: bool = False,
    ) -> list[Episode]:
        season = self.seasons[season_id]
        existing_numbers = {episode.number for episode in season.episodes}
        inserted: list[Episode] = []
        updated_episodes = list(season.episodes)
        for episode in episodes:
            if episode.number in existing_numbers:
                continue
            new_episode = episode.model_copy(update={"skipped": skipped})
            self.episodes[new_episode.id] = new_episode
            updated_episodes.append(new_episode)
            existing_numbers.add(episode.number)
            inserted.append(new_episode)
        self.seasons[season_id] = season.model_copy(
            update={"episodes": updated_episodes}
        )
        show = self.shows[season.show_id]
        updated_seasons = [
            self.seasons[season_id] if s.id == season_id else s for s in show.seasons
        ]
        self.shows[season.show_id] = show.model_copy(
            update={"seasons": updated_seasons}
        )
        return inserted

    async def get_episode_file_by_id(self, file_id: UUID) -> EpisodeFile | None:
        return self.episode_files.get(file_id)


class FakeMovieRepository:
    def __init__(self) -> None:
        self.db = FakeDb()
        self.get_movie_by_id_calls = 0
        self.movies: dict[MovieId, Movie] = {}
        self.movie_files: dict[UUID, MovieFile] = {}
        self.torrents_by_movie: dict[MovieId, list[Torrent]] = {}

    def add_movie(self, movie: Movie) -> Movie:
        self.movies[movie.id] = movie
        return movie

    async def get_movie_by_id(self, *, movie_id: MovieId) -> Movie | None:
        self.get_movie_by_id_calls += 1
        return self.movies.get(movie_id)

    async def get_movies_by_ids(self, movie_ids: list[MovieId]) -> dict[MovieId, Movie]:
        self.get_movies_by_ids_calls = getattr(self, "get_movies_by_ids_calls", 0) + 1
        self.last_movie_ids_batch = list(movie_ids)
        return {
            movie_id: self.movies[movie_id]
            for movie_id in movie_ids
            if movie_id in self.movies
        }

    async def get_movie_ids(self) -> list[MovieId]:
        return list(self.movies.keys())

    async def get_movie_files_for_movies(
        self, movie_ids: list[MovieId]
    ) -> dict[MovieId, list[MovieFile]]:
        grouped: dict[MovieId, list[MovieFile]] = {mid: [] for mid in movie_ids}
        wanted = set(movie_ids)
        for movie_file in self.movie_files.values():
            if movie_file.movie_id in wanted:
                grouped.setdefault(movie_file.movie_id, []).append(movie_file)
        return grouped

    async def get_movie_names_by_ids(
        self, movie_ids: list[MovieId]
    ) -> dict[MovieId, str]:
        self.name_batch_calls = getattr(self, "name_batch_calls", 0) + 1
        return {
            movie_id: self.movies[movie_id].name
            for movie_id in movie_ids
            if movie_id in self.movies
        }

    async def get_movie_files_by_movie_id(
        self, *, movie_id: MovieId
    ) -> list[MovieFile]:
        return [f for f in self.movie_files.values() if f.movie_id == movie_id]

    async def set_auto_download_backoff(
        self, movie_id: MovieId, until: datetime | None
    ) -> None:
        movie = self.movies.get(movie_id)
        if movie is not None:
            self.movies[movie_id] = movie.model_copy(
                update={"auto_download_backoff_until": until}
            )

    async def get_orphaned_failed_movie_files(self) -> list[MovieFile]:
        return [
            f
            for f in self.movie_files.values()
            if f.torrent_id is None
            and f.import_status
            in (ImportOutcome.failed_io, ImportOutcome.failed_no_match)
        ]

    async def update_movie_file_import_status(
        self,
        *,
        file_id: UUID,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        await self.update_movie_file_import_status_bulk(
            file_ids=[file_id],
            status=status,
            error=error,
        )

    async def update_movie_file_import_status_bulk(
        self,
        *,
        file_ids: list[UUID],
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        for file_id in file_ids:
            row = self.movie_files[file_id]
            self.movie_files[file_id] = row.model_copy(
                update={
                    "import_status": status,
                    "import_error": error,
                    "last_attempt_at": now,
                    "attempt_count": row.attempt_count + 1,
                    "imported_at": now
                    if status == ImportOutcome.imported
                    else row.imported_at,
                }
            )

    async def finalize_movie_file_import(
        self,
        *,
        file_id: UUID,
        quality: Quality,
        codec: str,
        hdr: bool,
        source: str,
        variant: str,
        extra: str,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        row = self.movie_files[file_id]
        now = datetime.now(UTC)
        self.movie_files[file_id] = row.model_copy(
            update={
                "quality": quality,
                "codec": codec,
                "hdr": hdr,
                "source": source,
                "variant": variant,
                "extra": extra,
                "import_status": status,
                "import_error": error,
                "last_attempt_at": now,
                "attempt_count": row.attempt_count + 1,
                "imported_at": now
                if status == ImportOutcome.imported
                else row.imported_at,
            }
        )

    async def add_movie_file(self, movie_file: MovieFile) -> MovieFile:
        self.movie_files[movie_file.id] = movie_file
        return movie_file

    async def get_movie_file_by_id(self, file_id: UUID) -> MovieFile | None:
        return self.movie_files.get(file_id)

    async def get_torrents_by_movie_id(self, *, movie_id: MovieId) -> list[Torrent]:
        return list(self.torrents_by_movie.get(movie_id, []))


class FakeTorrentRepository:
    def __init__(
        self,
        *,
        show_repo: FakeShowRepository | None = None,
        movie_repo: FakeMovieRepository | None = None,
    ) -> None:
        self.show_repo = show_repo
        self.movie_repo = movie_repo
        self.db = FakeDb()
        self.torrents: dict[TorrentId, Torrent] = {}
        self.episode_files: dict[TorrentId, list[EpisodeFile]] = {}
        self.movie_files: dict[TorrentId, list[MovieFile]] = {}
        self.show_of_torrent: dict[TorrentId, Show] = {}
        self.movie_of_torrent: dict[TorrentId, Movie] = {}
        self.blocked_hashes: set[str] = set()
        self.manual_parse_tokens: dict[uuid.UUID, dict] = {}

    async def pop_manual_parse_token(self, token_id: uuid.UUID) -> dict | None:
        return self.manual_parse_tokens.pop(token_id, None)

    @staticmethod
    def _normalize_hash(info_hash: str) -> str:
        return info_hash.strip().lower()

    async def is_hash_blocked(self, info_hash: str) -> bool:
        if not info_hash:
            return False
        return self._normalize_hash(info_hash) in self.blocked_hashes

    async def get_blocked_hashes(self, info_hashes: Sequence[str]) -> set[str]:
        normalized = {self._normalize_hash(h) for h in info_hashes if h and h.strip()}
        if not normalized:
            return set()
        self.get_blocked_hashes_calls = getattr(self, "get_blocked_hashes_calls", 0) + 1
        return normalized & self.blocked_hashes

    async def get_torrent_by_id(self, *, torrent_id: TorrentId) -> Torrent | None:
        return self.torrents.get(torrent_id)

    async def save_torrent(self, *, torrent: Torrent) -> Torrent:
        self.torrents[torrent.id] = torrent
        return torrent

    async def get_torrents_by_ids(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, Torrent]:
        self.get_torrents_by_ids_calls = (
            getattr(self, "get_torrents_by_ids_calls", 0) + 1
        )
        self.last_torrent_ids_batch = list(torrent_ids)
        return {
            torrent_id: self.torrents[torrent_id]
            for torrent_id in torrent_ids
            if torrent_id in self.torrents
        }

    async def get_episode_files_of_torrent(
        self, *, torrent_id: TorrentId
    ) -> list[EpisodeFile]:
        return list(self.episode_files.get(torrent_id, []))

    async def get_movie_files_of_torrent(
        self, *, torrent_id: TorrentId
    ) -> list[MovieFile]:
        return list(self.movie_files.get(torrent_id, []))

    async def get_episode_files_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> list[EpisodeFile]:
        out: list[EpisodeFile] = []
        for tid in torrent_ids:
            out.extend(self.episode_files.get(tid, []))
        return out

    async def get_movie_files_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> list[MovieFile]:
        out: list[MovieFile] = []
        for tid in torrent_ids:
            out.extend(self.movie_files.get(tid, []))
        return out

    async def get_show_of_torrent(self, torrent_id: TorrentId) -> Show | None:
        return self.show_of_torrent.get(torrent_id)

    async def get_movie_of_torrent(self, torrent_id: TorrentId) -> Movie | None:
        return self.movie_of_torrent.get(torrent_id)

    async def get_show_contexts_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, dict]:
        self.show_context_batch_calls = getattr(self, "show_context_batch_calls", 0) + 1
        if not torrent_ids or self.show_repo is None:
            return {}
        result: dict[TorrentId, dict] = {}
        for tid in torrent_ids:
            files = self.episode_files.get(tid, [])
            if not files:
                continue
            show = self.show_of_torrent.get(tid)
            if show is None:
                continue
            ctx = result.setdefault(
                tid,
                {
                    "show_id": show.id,
                    "show_name": show.name,
                    "show_year": show.year,
                    "metadata_provider": show.metadata_provider,
                    "seasons": set(),
                    "episodes": set(),
                    "variant": files[0].variant or "",
                },
            )
            for ef in files:
                episode = self.show_repo.episodes.get(ef.episode_id)
                if episode is None:
                    continue
                season = _season_for_episode(self.show_repo, ef.episode_id)
                ctx["seasons"].add(int(season.number))
                ctx["episodes"].add(int(episode.number))
        return result

    async def get_movie_contexts_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, dict]:
        self.movie_context_batch_calls = (
            getattr(self, "movie_context_batch_calls", 0) + 1
        )
        if not torrent_ids:
            return {}
        result: dict[TorrentId, dict] = {}
        for tid in torrent_ids:
            files = self.movie_files.get(tid, [])
            if not files:
                continue
            movie = self.movie_of_torrent.get(tid)
            if movie is None:
                continue
            if tid in result:
                continue
            result[tid] = {
                "movie_id": movie.id,
                "movie_name": movie.name,
                "movie_year": movie.year,
                "metadata_provider": movie.metadata_provider,
                "variant": files[0].variant or "",
            }
        return result

    async def get_import_status_aggregates_for_torrents(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, list]:
        self.import_status_batch_calls = (
            getattr(self, "import_status_batch_calls", 0) + 1
        )
        if not torrent_ids:
            return {}
        result: dict[TorrentId, list] = {tid: [] for tid in torrent_ids}
        for tid in torrent_ids:
            for ef in self.episode_files.get(tid, []):
                result[tid].append(
                    (ef.import_status, ef.import_error, ef.last_attempt_at)
                )
            for mf in self.movie_files.get(tid, []):
                result[tid].append(
                    (mf.import_status, mf.import_error, mf.last_attempt_at)
                )
        return result

    async def get_all_torrents(self) -> list[Torrent]:
        return list(self.torrents.values())

    async def get_active_torrents(self) -> list[Torrent]:
        from miramedia.torrents.repository import ACTIVE_TORRENT_STATUSES

        return [
            torrent
            for torrent in self.torrents.values()
            if torrent.status in ACTIVE_TORRENT_STATUSES
        ]

    async def get_finished_torrents(self) -> list[Torrent]:
        return [
            torrent
            for torrent in self.torrents.values()
            if torrent.status == TorrentStatus.finished
        ]

    async def paginate_sha1_mismatch_keys(
        self, *, offset: int, limit: int
    ) -> Sha1MismatchPage:
        show_rows: list[EpisodeFile] = []
        movie_rows: list[MovieFile] = []
        if self.show_repo is not None and hasattr(
            self.show_repo, "list_sha1_mismatch_files"
        ):
            show_rows = await self.show_repo.list_sha1_mismatch_files(
                offset=0, limit=10_000
            )
        if self.movie_repo is not None and hasattr(
            self.movie_repo, "list_sha1_mismatch_files"
        ):
            movie_rows = await self.movie_repo.list_sha1_mismatch_files(
                offset=0, limit=10_000
            )
        keys = [Sha1MismatchPageKey("show", row.id) for row in show_rows] + [
            Sha1MismatchPageKey("movie", row.id) for row in movie_rows
        ]
        total = len(keys)
        page = keys[offset : offset + limit]
        return Sha1MismatchPage(keys=page, total=total)


def _season_for_episode(repo: FakeShowRepository, episode_id: EpisodeId) -> Season:
    for season in repo.seasons.values():
        if any(ep.id == episode_id for ep in season.episodes):
            return season
    msg = f"season not found for episode {episode_id}"
    raise KeyError(msg)


def make_show(
    *,
    name: str = "Test Show",
    year: int = 2020,
    season_number: int = 1,
    episode_number: int = 1,
    air_date=None,
    skipped: bool = False,
    continuous_download: bool | None = None,
    auto_download_backoff_until: datetime | None = None,
) -> Show:
    episode_id = EpisodeId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    show_id = ShowId(uuid.uuid4())
    episode = Episode(
        id=episode_id,
        number=EpisodeNumber(episode_number),
        title="Pilot",
        air_date=air_date,
    )
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(season_number),
        episodes=[episode],
    )
    return Show(
        id=show_id,
        name=name,
        overview="",
        year=year,
        external_id="ext-1",
        metadata_provider="native",
        skipped=skipped,
        continuous_download=continuous_download,
        auto_download_backoff_until=auto_download_backoff_until,
        seasons=[season],
    )


def make_movie(
    *,
    name: str = "Test Movie",
    year: int = 2020,
    skipped: bool = False,
    continuous_download: bool | None = None,
    quality_upgrades: bool | None = None,
    upgrade_until_quality: str | None = None,
    release_date=None,
    auto_download_backoff_until: datetime | None = None,
) -> Movie:
    return Movie(
        id=MovieId(uuid.uuid4()),
        name=name,
        overview="",
        year=year,
        external_id="ext-m1",
        metadata_provider="native",
        skipped=skipped,
        continuous_download=continuous_download,
        quality_upgrades=quality_upgrades,
        upgrade_until_quality=upgrade_until_quality,
        release_date=release_date,
        auto_download_backoff_until=auto_download_backoff_until,
    )


def make_torrent(*, title: str = "Test.Show.S01E01.1080p") -> Torrent:
    return Torrent(
        id=TorrentId(uuid.uuid4()),
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.fullhd,
        hash="a" * 40,
    )


class FakeRequestRepository:
    """In-memory stand-in for ``RequestRepository`` Seerr sync surface."""

    def __init__(self) -> None:
        self.db = FakeDb()
        self.by_id: dict[MediaRequestId, MediaRequest] = {}
        self.by_seerr_id: dict[int, MediaRequest] = {}
        self.upsert_calls: list[MediaRequest] = []
        self.update_calls: list[tuple[MediaRequestId, dict]] = []

    def seed(self, request: MediaRequest) -> MediaRequest:
        self.by_id[request.id] = request
        if request.seerr_request_id is not None:
            self.by_seerr_id[request.seerr_request_id] = request
        return request

    async def get_by_seerr_request_id(
        self, seerr_request_id: int
    ) -> MediaRequest | None:
        return self.by_seerr_id.get(seerr_request_id)

    async def upsert_seerr_request(self, request: MediaRequest) -> MediaRequest:
        self.upsert_calls.append(request)
        existing = (
            self.by_seerr_id.get(request.seerr_request_id)
            if request.seerr_request_id is not None
            else None
        )
        if existing is None:
            self.by_id[request.id] = request
            if request.seerr_request_id is not None:
                self.by_seerr_id[request.seerr_request_id] = request
            return request
        updated = existing.model_copy(
            update={
                "title": request.title,
                "imdb_id": request.imdb_id or existing.imdb_id,
                "external_id": request.external_id or existing.external_id,
                "status": request.status,
                "tmdb_id": request.tmdb_id or existing.tmdb_id,
                "seerr_media_id": request.seerr_media_id,
                "source": request.source,
            }
        )
        self.by_id[updated.id] = updated
        if updated.seerr_request_id is not None:
            self.by_seerr_id[updated.seerr_request_id] = updated
        return updated

    async def list_native_unsynced(self) -> list[MediaRequest]:
        return [
            row
            for row in self.by_id.values()
            if row.source == RequestSource.native
            and row.seerr_request_id is None
            and row.status in (RequestStatus.pending, RequestStatus.approved)
        ]

    async def update_request(
        self, request_id: MediaRequestId, **kwargs: object
    ) -> MediaRequest:
        self.update_calls.append((request_id, dict(kwargs)))
        row = self.by_id[request_id]
        updated = row.model_copy(update=kwargs)
        self.by_id[request_id] = updated
        if updated.seerr_request_id is not None:
            self.by_seerr_id[updated.seerr_request_id] = updated
        return updated

    async def save_request(self, request: MediaRequest) -> MediaRequest:
        self.by_id[request.id] = request
        return request

    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        return self.by_id[request_id]


class FakeSettingsRepository:
    """In-memory stand-in for ``SettingsRepository``."""

    def __init__(
        self,
        overrides: dict | None = None,
        *,
        revision: int | None = None,
    ) -> None:
        self.db = FakeDb()
        self.overrides: dict = copy.deepcopy(overrides or {})
        if revision is not None:
            self.revision = revision
        elif self.overrides:
            self.revision = 1
        else:
            self.revision = 0
        self.save_calls: list[dict] = []
        self.cas_calls: list[tuple[dict, int]] = []
        self.reset_called = False
        self.clear_path_calls: list[list[str]] = []
        self._insert_lost_race = False

    async def get_overrides(self) -> dict:
        from miramedia.settings.normalize import normalize_stored_overrides

        return normalize_stored_overrides(self.overrides)

    async def get_overrides_with_revision(self) -> tuple[dict, int]:
        from miramedia.settings.normalize import normalize_stored_overrides

        return normalize_stored_overrides(self.overrides), self.revision

    async def save_overrides_cas(
        self,
        overrides: dict,
        expected_revision: int,
    ) -> tuple[dict, int]:
        from miramedia.settings.repository import SettingsRevisionConflictError

        if expected_revision == 0:
            if self._insert_lost_race and self.revision == 0:
                self.revision = 1
                self.overrides = copy.deepcopy(overrides)
                self.cas_calls.append((copy.deepcopy(overrides), 0))
                self.save_calls.append(copy.deepcopy(overrides))
                raise SettingsRevisionConflictError(0, 1)
            if self.revision == 0:
                self.cas_calls.append((copy.deepcopy(overrides), 0))
                self.save_calls.append(copy.deepcopy(overrides))
                self.overrides = copy.deepcopy(overrides)
                self.revision = 1
                return self.overrides, self.revision
            raise SettingsRevisionConflictError(0, self.revision)

        if expected_revision != self.revision:
            raise SettingsRevisionConflictError(expected_revision, self.revision)
        self.cas_calls.append((copy.deepcopy(overrides), expected_revision))
        self.save_calls.append(copy.deepcopy(overrides))
        self.overrides = copy.deepcopy(overrides)
        self.revision += 1
        if not overrides:
            self.reset_called = True
        return self.overrides, self.revision

    async def fetch_overrides_with_revision(self) -> tuple[dict, int]:
        return await self.get_overrides_with_revision()

    async def reset_overrides(self) -> None:
        self.reset_called = True
        self.overrides = {}

    async def clear_override_path(self, path: list[str]) -> dict:
        from miramedia.settings.service import compute_clear_override_path

        self.clear_path_calls.append(list(path))
        updated = compute_clear_override_path(self.overrides, path)
        saved, _revision = await self.save_overrides_cas(updated, self.revision)
        return saved


class FakePlaybackRepository:
    def __init__(self) -> None:
        from unittest.mock import MagicMock

        self.db = MagicMock()
        self.progress: dict[tuple[UUID, UUID], PlaybackProgress] = {}
        self.watch_states: dict[tuple[UUID, str, UUID], WatchState] = {}
        self.logical_media: dict[tuple[UUID, MediaKind], UUID] = {}

    def _progress_key(self, user_id: UUID, file_id: UUID) -> tuple[UUID, UUID]:
        return (user_id, file_id)

    def _watch_key(
        self, user_id: UUID, media_kind: MediaKind, media_id: UUID
    ) -> tuple[UUID, str, UUID]:
        return (user_id, media_kind.value, media_id)

    def seed_logical_media(
        self, *, file_id: UUID, media_kind: MediaKind, media_id: UUID
    ) -> None:
        self.logical_media[(file_id, media_kind)] = media_id

    async def get_logical_media_id(
        self, *, file_id: UUID, media_kind: MediaKind
    ) -> UUID:
        media_id = self.logical_media.get((file_id, media_kind))
        if media_id is None:
            msg = f"{media_kind.value} file missing logical media id"
            raise RuntimeError(msg)
        return media_id

    async def get_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
        media_kind: MediaKind | None = None,
    ) -> PlaybackProgress | None:
        progress = self.progress.get(self._progress_key(user_id, file_id))
        if progress is None:
            return None
        if media_kind is not None and progress.media_kind != media_kind:
            return None
        return progress

    async def upsert_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
        media_kind: MediaKind,
        position_ms: int,
        duration_ms: int,
        completed: bool,
    ) -> PlaybackProgress:
        progress = PlaybackProgress(
            file_id=file_id,
            media_kind=media_kind,
            position_ms=position_ms,
            duration_ms=duration_ms,
            completed=completed,
            updated_at=datetime.now(UTC),
        )
        self.progress[self._progress_key(user_id, file_id)] = progress
        media_id = self.logical_media.get((file_id, media_kind))
        if media_id is not None:
            await self._sync_derived_watch_state(
                user_id=user_id,
                media_kind=media_kind,
                media_id=media_id,
            )
        return progress

    async def delete_progress(
        self,
        *,
        user_id: UUID,
        file_id: UUID,
    ) -> None:
        key = self._progress_key(user_id, file_id)
        progress = self.progress.pop(key, None)
        if progress is not None:
            media_id = self.logical_media.get((file_id, progress.media_kind))
            if media_id is not None:
                await self._sync_derived_watch_state(
                    user_id=user_id,
                    media_kind=progress.media_kind,
                    media_id=media_id,
                )

    async def delete_all_progress(self, *, user_id: UUID) -> None:
        self.progress = {
            key: value for key, value in self.progress.items() if key[0] != user_id
        }

    async def delete_all_viewing_state(self, *, user_id: UUID) -> None:
        self.progress = {
            key: value for key, value in self.progress.items() if key[0] != user_id
        }
        self.watch_states = {
            key: value for key, value in self.watch_states.items() if key[0] != user_id
        }

    def _has_completed_progress(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> bool:
        for (row_user_id, file_id), progress in self.progress.items():
            if row_user_id != user_id or not progress.completed:
                continue
            logical = self.logical_media.get((file_id, media_kind))
            if logical == media_id and progress.media_kind == media_kind:
                return True
        return False

    async def _sync_derived_watch_state(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> None:
        key = self._watch_key(user_id, media_kind, media_id)
        existing = self.watch_states.get(key)
        if existing is not None and existing.source == "manual":
            return
        if self._has_completed_progress(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        ):
            self.watch_states[key] = WatchState(
                media_kind="movie" if media_kind == MediaKind.movie else "episode",
                media_id=media_id,
                watched=True,
                source="derived",
                watched_at=datetime.now(UTC),
            )
            return
        if existing is not None and existing.source == "derived":
            del self.watch_states[key]

    async def get_watched(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> WatchState:
        key = self._watch_key(user_id, media_kind, media_id)
        row = self.watch_states.get(key)
        if row is not None and row.source == "manual":
            return row
        if self._has_completed_progress(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        ):
            return WatchState(
                media_kind="movie" if media_kind == MediaKind.movie else "episode",
                media_id=media_id,
                watched=True,
                source="derived",
                watched_at=row.watched_at if row is not None else datetime.now(UTC),
            )
        return WatchState(
            media_kind="movie" if media_kind == MediaKind.movie else "episode",
            media_id=media_id,
            watched=False,
            source=None,
            watched_at=None,
        )

    async def set_watched(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
        watched: bool,
    ) -> WatchState:
        key = self._watch_key(user_id, media_kind, media_id)
        state = WatchState(
            media_kind="movie" if media_kind == MediaKind.movie else "episode",
            media_id=media_id,
            watched=watched,
            source="manual",
            watched_at=datetime.now(UTC) if watched else None,
        )
        self.watch_states[key] = state
        return state

    async def clear_watched_override(
        self,
        *,
        user_id: UUID,
        media_kind: MediaKind,
        media_id: UUID,
    ) -> WatchState:
        key = self._watch_key(user_id, media_kind, media_id)
        row = self.watch_states.get(key)
        if row is not None and row.source == "manual":
            del self.watch_states[key]
        return await self.get_watched(
            user_id=user_id,
            media_kind=media_kind,
            media_id=media_id,
        )

    async def set_episodes_watched(
        self,
        *,
        user_id: UUID,
        episode_ids: list[UUID],
        watched: bool,
    ) -> None:
        for episode_id in episode_ids:
            await self.set_watched(
                user_id=user_id,
                media_kind=MediaKind.episode,
                media_id=episode_id,
                watched=watched,
            )

    async def list_continue(
        self,
        *,
        user_id: UUID,
        limit: int,
    ) -> list:
        from miramedia.playback.schemas import ContinueWatchingItem

        items: list[ContinueWatchingItem] = []
        for (row_user_id, _), progress in self.progress.items():
            if row_user_id != user_id or progress.completed:
                continue
            items.append(
                ContinueWatchingItem(
                    file_id=progress.file_id,
                    media_kind=progress.media_kind,
                    media_id=progress.file_id,
                    title="stub",
                    poster_media_id=progress.file_id,
                    position_ms=progress.position_ms,
                    duration_ms=progress.duration_ms,
                    updated_at=progress.updated_at,
                )
            )
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items[:limit]


@dataclass
class _FakeWatchlistRow:
    id: UUID
    user_id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class _FakeWatchlistItemRow:
    id: UUID
    watchlist_id: UUID
    position: int
    media_kind: str
    media_id: UUID


class FakeWatchlistRepository:
    def __init__(self) -> None:
        self.watchlists: dict[UUID, _FakeWatchlistRow] = {}
        self.items: dict[UUID, _FakeWatchlistItemRow] = {}

    def items_for(self, watchlist_id: UUID) -> list[_FakeWatchlistItemRow]:
        return [
            item for item in self.items.values() if item.watchlist_id == watchlist_id
        ]

    async def list_summaries(self, *, user_id: UUID):
        from miramedia.watchlists.schemas import WatchlistSummary

        rows = [row for row in self.watchlists.values() if row.user_id == user_id]
        rows.sort(key=lambda row: row.name.casefold())
        return [
            WatchlistSummary(
                id=row.id,
                name=row.name,
                description=row.description,
                item_count=len(self.items_for(row.id)),
                cover_poster_media_id=self._cover_poster_for(row.id),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def _cover_poster_for(self, watchlist_id: UUID):
        items = sorted(
            self.items_for(watchlist_id),
            key=lambda item: (item.position, item.id),
        )
        if not items:
            return None
        # Fake item views use media_id as poster_media_id.
        return items[0].media_id

    async def name_taken(
        self,
        *,
        user_id: UUID,
        name: str,
        exclude_watchlist_id: UUID | None = None,
    ) -> bool:
        for row in self.watchlists.values():
            if row.user_id != user_id:
                continue
            if exclude_watchlist_id is not None and row.id == exclude_watchlist_id:
                continue
            if row.name.casefold() == name.casefold():
                return True
        return False

    async def create(
        self,
        *,
        user_id: UUID,
        name: str,
        description: str | None,
    ):
        now = datetime.now(UTC)
        row = _FakeWatchlistRow(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self.watchlists[row.id] = row
        return row

    async def get_owned(self, *, user_id: UUID, watchlist_id: UUID):
        row = self.watchlists.get(watchlist_id)
        if row is None or row.user_id != user_id:
            return None
        return row

    async def get_detail(self, *, user_id: UUID, watchlist_id: UUID):
        from miramedia.watchlists.schemas import WatchlistDetail, WatchlistItemView

        row = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if row is None:
            return None
        items = sorted(
            self.items_for(watchlist_id),
            key=lambda item: (item.position, item.id),
        )
        return WatchlistDetail(
            id=row.id,
            name=row.name,
            description=row.description,
            items=[
                WatchlistItemView(
                    id=item.id,
                    position=item.position,
                    media_kind=item.media_kind,
                    media_id=item.media_id,
                    title="stub",
                    poster_media_id=item.media_id,
                    watched=False,
                )
                for item in items
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def update(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        name: str | None,
        description: str | object | None = ...,
    ):
        row = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if description is not ...:
            row.description = description  # type: ignore[assignment]
        row.updated_at = datetime.now(UTC)
        return row

    async def delete(self, *, user_id: UUID, watchlist_id: UUID) -> bool:
        row = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if row is None:
            return False
        del self.watchlists[watchlist_id]
        for item_id, item in list(self.items.items()):
            if item.watchlist_id == watchlist_id:
                del self.items[item_id]
        return True

    async def add_item(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        media_kind: str,
        media_id: UUID,
    ):
        from miramedia.watchlists.schemas import WatchlistItemView

        row = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if row is None:
            return None
        for item in self.items_for(watchlist_id):
            if item.media_kind == media_kind and item.media_id == media_id:
                view = WatchlistItemView(
                    id=item.id,
                    position=item.position,
                    media_kind=item.media_kind,
                    media_id=item.media_id,
                    title="stub",
                    poster_media_id=item.media_id,
                    watched=False,
                )
                return view, False
        position = len(self.items_for(watchlist_id))
        item = _FakeWatchlistItemRow(
            id=uuid.uuid4(),
            watchlist_id=watchlist_id,
            position=position,
            media_kind=media_kind,
            media_id=media_id,
        )
        self.items[item.id] = item
        row.updated_at = datetime.now(UTC)
        view = WatchlistItemView(
            id=item.id,
            position=item.position,
            media_kind=item.media_kind,
            media_id=item.media_id,
            title="stub",
            poster_media_id=item.media_id,
            watched=False,
        )
        return view, True

    async def reorder_items(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        item_ids: list[UUID],
    ):
        row = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if row is None:
            return None
        current_ids = {item.id for item in self.items_for(watchlist_id)}
        if set(item_ids) != current_ids or len(item_ids) != len(current_ids):
            return None
        for position, item_id in enumerate(item_ids):
            self.items[item_id].position = position
        row.updated_at = datetime.now(UTC)
        return await self.get_detail(user_id=user_id, watchlist_id=watchlist_id)

    async def remove_item(
        self,
        *,
        user_id: UUID,
        watchlist_id: UUID,
        item_id: UUID,
    ) -> bool:
        row = await self.get_owned(user_id=user_id, watchlist_id=watchlist_id)
        if row is None:
            return False
        item = self.items.get(item_id)
        if item is None or item.watchlist_id != watchlist_id:
            return False
        del self.items[item_id]
        row.updated_at = datetime.now(UTC)
        return True

    async def delete_items_for_media(
        self,
        *,
        user_id: UUID,
        media_kind: str,
        media_id: UUID,
    ) -> int:
        removed = 0
        for item_id, item in list(self.items.items()):
            owner = self.watchlists.get(item.watchlist_id)
            if owner is None or owner.user_id != user_id:
                continue
            if item.media_kind == media_kind and item.media_id == media_id:
                del self.items[item_id]
                removed += 1
        return removed

    async def delete_items_for_media_ids(
        self,
        *,
        user_id: UUID,
        media_kind: str,
        media_ids: list[UUID],
    ) -> int:
        if not media_ids:
            return 0
        target_ids = set(media_ids)
        removed = 0
        for item_id, item in list(self.items.items()):
            owner = self.watchlists.get(item.watchlist_id)
            if owner is None or owner.user_id != user_id:
                continue
            if item.media_kind == media_kind and item.media_id in target_ids:
                del self.items[item_id]
                removed += 1
        return removed

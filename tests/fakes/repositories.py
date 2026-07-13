"""Dict-backed repository fakes with real in-memory state."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from uuid import UUID

from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import Movie, MovieFile, MovieId
from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestId,
    RequestSource,
    RequestStatus,
)
from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.schemas import Quality, Torrent, TorrentId, TorrentStatus
from tests.fakes.db import FakeDb


class FakeShowRepository:
    def __init__(self) -> None:
        self.db = FakeDb()
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
        return self.shows.get(show_id)

    async def get_episode(self, *, episode_id: EpisodeId) -> Episode:
        return self.episodes[episode_id]

    async def get_season_by_episode(self, *, episode_id: EpisodeId) -> Season:
        for season in self.seasons.values():
            if any(ep.id == episode_id for ep in season.episodes):
                return season
        msg = f"season not found for episode {episode_id}"
        raise KeyError(msg)

    async def batch_episodes_with_context(
        self, episode_ids: list[EpisodeId]
    ) -> dict[EpisodeId, object]:
        from miramedia.shows.repository import EpisodeIntegrityContext

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

    async def get_torrents_by_show_id(self, *, show_id: ShowId) -> list[Torrent]:
        return list(self.torrents_by_show.get(show_id, []))

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
        row = self.episode_files[file_id]
        now = datetime.now(UTC)
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

    async def add_episode_file(self, *, episode_file: EpisodeFile) -> EpisodeFile:
        self.episode_files[episode_file.id] = episode_file
        episode = self.episodes[episode_file.episode_id]
        episode.episode_files = [*episode.episode_files, episode_file]
        return episode_file

    async def get_episode_file_by_id(self, file_id: UUID) -> EpisodeFile | None:
        return self.episode_files.get(file_id)


class FakeMovieRepository:
    def __init__(self) -> None:
        self.db = FakeDb()
        self.movies: dict[MovieId, Movie] = {}
        self.movie_files: dict[UUID, MovieFile] = {}
        self.torrents_by_movie: dict[MovieId, list[Torrent]] = {}

    def add_movie(self, movie: Movie) -> Movie:
        self.movies[movie.id] = movie
        return movie

    async def get_movie_by_id(self, *, movie_id: MovieId) -> Movie | None:
        return self.movies.get(movie_id)

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
        row = self.movie_files[file_id]
        now = datetime.now(UTC)
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

    async def add_movie_file(self, *, movie_file: MovieFile) -> MovieFile:
        self.movie_files[movie_file.id] = movie_file
        return movie_file

    async def get_movie_file_by_id(self, file_id: UUID) -> MovieFile | None:
        return self.movie_files.get(file_id)


class FakeTorrentRepository:
    def __init__(self) -> None:
        self.torrents: dict[TorrentId, Torrent] = {}
        self.episode_files: dict[TorrentId, list[EpisodeFile]] = {}
        self.movie_files: dict[TorrentId, list[MovieFile]] = {}
        self.show_of_torrent: dict[TorrentId, Show] = {}
        self.movie_of_torrent: dict[TorrentId, Movie] = {}

    async def get_torrent_by_id(self, *, torrent_id: TorrentId) -> Torrent | None:
        return self.torrents.get(torrent_id)

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

    async def get_all_torrents(self) -> list[Torrent]:
        return list(self.torrents.values())


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
        seasons=[season],
    )


def make_movie(
    *,
    name: str = "Test Movie",
    year: int = 2020,
    skipped: bool = False,
    continuous_download: bool | None = None,
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

    def __init__(self, overrides: dict | None = None) -> None:
        self.db = FakeDb()
        self.overrides: dict = copy.deepcopy(overrides or {})
        self.save_calls: list[dict] = []
        self.reset_called = False
        self.clear_path_calls: list[list[str]] = []

    async def get_overrides(self) -> dict:
        return copy.deepcopy(self.overrides)

    async def save_overrides(self, overrides: dict) -> dict:
        self.save_calls.append(copy.deepcopy(overrides))
        self.overrides = copy.deepcopy(overrides)
        return self.overrides

    async def reset_overrides(self) -> None:
        self.reset_called = True
        self.overrides = {}

    async def clear_override_path(self, path: list[str]) -> dict:
        self.clear_path_calls.append(list(path))
        if not path:
            return await self.get_overrides()
        overrides = await self.get_overrides()
        node = overrides
        stack: list[tuple[dict, str]] = []
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                return overrides
            stack.append((node, key))
            node = node[key]
        if not isinstance(node, dict) or path[-1] not in node:
            return overrides
        del node[path[-1]]
        for parent, key in reversed(stack):
            if isinstance(parent[key], dict) and not parent[key]:
                del parent[key]
        self.overrides = overrides
        return overrides

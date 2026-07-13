"""Dict-backed repository fakes with real in-memory state."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from uuid import UUID

from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import Movie, MovieFile, MovieId
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

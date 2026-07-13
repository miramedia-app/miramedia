"""DB-free tests for the SHA1 integrity-mismatch API."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.exceptions import NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieFile
from miramedia.shows.schemas import EpisodeFile
from miramedia.torrents.integrity import (
    INTEGRITY_MISMATCH_DEFAULT_LIMIT,
    INTEGRITY_MISMATCH_MAX_LIMIT,
)
from miramedia.torrents.schemas import IntegrityMismatch, MediaType, Quality
from miramedia.torrents.service import TorrentService
from tests.fakes.db import FakeDb
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
    make_movie,
    make_show,
)
from tests.fakes.scheduler import background_session_factory

PREFIX = "/api/v1/torrents"


def _run(coro):
    return asyncio.run(coro)


def _torrent_service(
    show_repo: _IntegrityShowRepo,
    movie_repo: _IntegrityMovieRepo,
) -> TorrentService:
    torrent_repo = FakeTorrentRepository(show_repo=show_repo, movie_repo=movie_repo)
    torrent_repo.db = FakeDb()
    return TorrentService(torrent_repository=torrent_repo)  # type: ignore[arg-type]


class _IntegrityShowRepo(FakeShowRepository):
    async def get_episode_file_by_id(self, file_id: uuid.UUID):
        return self.episode_files.get(file_id)

    async def list_sha1_mismatch_files(
        self, *, offset: int = 0, limit: int
    ) -> list[EpisodeFile]:
        rows = sorted(
            (
                f
                for f in self.episode_files.values()
                if f.import_status == ImportOutcome.imported
                and (f.import_error or "").startswith("sha1 mismatch")
            ),
            key=lambda f: f.id,
        )
        return rows[offset : offset + limit]

    async def count_sha1_mismatch_files(self) -> int:
        return len(
            [
                f
                for f in self.episode_files.values()
                if f.import_status == ImportOutcome.imported
                and (f.import_error or "").startswith("sha1 mismatch")
            ]
        )

    async def get_sha1_mismatch_episode_files_by_ids(
        self, file_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, EpisodeFile]:
        out: dict[uuid.UUID, EpisodeFile] = {}
        for file_id in file_ids:
            row = self.episode_files.get(file_id)
            if row is None:
                continue
            if row.import_status == ImportOutcome.imported and (
                row.import_error or ""
            ).startswith("sha1 mismatch"):
                out[file_id] = row
        return out

    async def get_shows_by_ids(self, show_ids):
        from miramedia.shows.schemas import ShowId

        self.shows_by_ids_calls = getattr(self, "shows_by_ids_calls", 0) + 1
        return {ShowId(sid): self.shows[sid] for sid in show_ids if sid in self.shows}

    async def clear_file_integrity_state(
        self,
        file_id: uuid.UUID,
        *,
        expected_sha1: str | None,
        expected_import_error: str,
        reset_sha1: bool,
    ) -> bool:
        row = self.episode_files.get(file_id)
        if row is None:
            return False
        import_error = row.import_error or ""
        if (
            row.import_status != ImportOutcome.imported
            or not import_error.startswith("sha1 mismatch")
            or row.sha1 != expected_sha1
            or import_error != expected_import_error
        ):
            return False
        update: dict[str, Any] = {"import_error": None}
        if reset_sha1:
            update["sha1"] = None
        self.episode_files[file_id] = row.model_copy(update=update)
        return True


class _IntegrityMovieRepo(FakeMovieRepository):
    async def get_movie_file_by_id(self, file_id: uuid.UUID):
        return self.movie_files.get(file_id)

    async def list_sha1_mismatch_files(
        self, *, offset: int = 0, limit: int
    ) -> list[MovieFile]:
        rows = sorted(
            (
                f
                for f in self.movie_files.values()
                if f.import_status == ImportOutcome.imported
                and (f.import_error or "").startswith("sha1 mismatch")
            ),
            key=lambda f: f.id,
        )
        return rows[offset : offset + limit]

    async def count_sha1_mismatch_files(self) -> int:
        return len(
            [
                f
                for f in self.movie_files.values()
                if f.import_status == ImportOutcome.imported
                and (f.import_error or "").startswith("sha1 mismatch")
            ]
        )

    async def get_sha1_mismatch_movie_files_by_ids(
        self, file_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, MovieFile]:
        out: dict[uuid.UUID, MovieFile] = {}
        for file_id in file_ids:
            row = self.movie_files.get(file_id)
            if row is None:
                continue
            if row.import_status == ImportOutcome.imported and (
                row.import_error or ""
            ).startswith("sha1 mismatch"):
                out[file_id] = row
        return out

    async def get_movies_by_ids(self, movie_ids):
        self.movies_by_ids_calls = getattr(self, "movies_by_ids_calls", 0) + 1
        return {mid: self.movies[mid] for mid in movie_ids if mid in self.movies}

    async def clear_file_integrity_state(
        self,
        file_id: uuid.UUID,
        *,
        expected_sha1: str | None,
        expected_import_error: str,
        reset_sha1: bool,
    ) -> bool:
        row = self.movie_files.get(file_id)
        if row is None:
            return False
        import_error = row.import_error or ""
        if (
            row.import_status != ImportOutcome.imported
            or not import_error.startswith("sha1 mismatch")
            or row.sha1 != expected_sha1
            or import_error != expected_import_error
        ):
            return False
        update: dict[str, Any] = {"import_error": None}
        if reset_sha1:
            update["sha1"] = None
        self.movie_files[file_id] = row.model_copy(update=update)
        return True


def _show_service(repo: _IntegrityShowRepo, path_by_id: dict[uuid.UUID, Path | None]):
    async def resolve_episode_file_path(row: EpisodeFile) -> Path | None:
        return path_by_id.get(row.id)

    async def batch_resolve_episode_file_paths(rows, episode_context, shows):
        del episode_context, shows
        return {row.id: path_by_id.get(row.id) for row in rows}

    return MagicMock(
        show_repository=repo,
        resolve_episode_file_path=resolve_episode_file_path,
        batch_resolve_episode_file_paths=batch_resolve_episode_file_paths,
    )


def _movie_service(repo: _IntegrityMovieRepo, path_by_id: dict[uuid.UUID, Path | None]):
    async def resolve_movie_file_path(row: MovieFile) -> Path | None:
        return path_by_id.get(row.id)

    async def batch_resolve_movie_file_paths(rows, movies):
        del movies
        return {row.id: path_by_id.get(row.id) for row in rows}

    return MagicMock(
        movie_repository=repo,
        resolve_movie_file_path=resolve_movie_file_path,
        batch_resolve_movie_file_paths=batch_resolve_movie_file_paths,
    )


def _patch_list_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    show_paths: dict[uuid.UUID, Path | None] | None = None,
    movie_paths: dict[uuid.UUID, Path | None] | None = None,
) -> None:
    show_map = show_paths or {}
    movie_map = movie_paths or {}

    async def _episode_paths(rows, episode_context, shows, layout):  # noqa: ARG001
        return {row.id: show_map.get(row.id) for row in rows}

    async def _movie_paths(rows, movies, layout):  # noqa: ARG001
        return {row.id: movie_map.get(row.id) for row in rows}

    monkeypatch.setattr(
        "miramedia.torrents.service.batch_resolve_episode_paths_async",
        _episode_paths,
    )
    monkeypatch.setattr(
        "miramedia.torrents.service.batch_resolve_movie_paths_async",
        _movie_paths,
    )


def test_list_integrity_mismatches_maps_show_and_movie_shape(monkeypatch) -> None:
    show = make_show(name="Severance", season_number=3, episode_number=7)
    episode = show.seasons[0].episodes[0]
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    ep_file = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=None,
        variant="remux",
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected abcdef1234…, got deadbeef00…)",
        last_attempt_at=detected,
        sha1="abcdef1234",
    )
    show_repo.episode_files[ep_file.id] = ep_file

    movie = make_movie(name="Dune")
    movie_repo = _IntegrityMovieRepo()
    movie_repo.add_movie(movie)
    movie_file = MovieFile(
        id=uuid.uuid4(),
        movie_id=movie.id,
        quality=Quality.uhd,
        variant="",
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected 1111111111…, got 2222222222…)",
        last_attempt_at=detected,
        sha1="1111111111",
    )
    movie_repo.movie_files[movie_file.id] = movie_file

    _patch_list_paths(
        monkeypatch,
        show_paths={ep_file.id: Path("/lib/S03E07.mkv")},
        movie_paths={movie_file.id: None},
    )
    svc = _torrent_service(show_repo, movie_repo)
    page = _run(
        svc.list_integrity_mismatches(
            offset=0,
            limit=INTEGRITY_MISMATCH_MAX_LIMIT,
            show_service=_show_service(
                show_repo, {ep_file.id: Path("/lib/S03E07.mkv")}
            ),
            movie_service=_movie_service(movie_repo, {movie_file.id: None}),
        )
    )
    rows = page.items

    assert len(rows) == 2
    show_row = next(r for r in rows if r.media_type == "show")
    movie_row = next(r for r in rows if r.media_type == "movie")

    assert show_row == IntegrityMismatch(
        file_id=ep_file.id,
        media_type="show",
        media_title="Severance",
        episode="S03E07",
        path="/lib/S03E07.mkv",
        quality=Quality.fullhd,
        variant_tag="remux",
        import_error=ep_file.import_error or "",
        detected_at=detected,
    )
    assert movie_row.media_title == "Dune"
    assert movie_row.episode is None
    assert movie_row.path is None
    assert movie_row.variant_tag == ""


def test_list_integrity_mismatches_batches_lookups(monkeypatch) -> None:
    """N mismatch rows must not issue O(N) title lookups."""
    show_a = make_show(name="Severance", season_number=1, episode_number=1)
    show_b = make_show(name="The Bear", season_number=2, episode_number=3)
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show_a)
    show_repo.add_show(show_b)
    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    path_by_id: dict[uuid.UUID, Path | None] = {}
    for show, quality, variant in (
        (show_a, Quality.fullhd, "remux"),
        (show_a, Quality.hd, ""),
        (show_b, Quality.uhd, "web"),
    ):
        episode = show.seasons[0].episodes[0]
        ep_file = EpisodeFile(
            id=uuid.uuid4(),
            episode_id=episode.id,
            quality=quality,
            torrent_id=None,
            variant=variant,
            import_status=ImportOutcome.imported,
            import_error="sha1 mismatch (expected a…, got b…)",
            last_attempt_at=detected,
            sha1="abc",
        )
        show_repo.episode_files[ep_file.id] = ep_file
        path_by_id[ep_file.id] = Path(f"/lib/{show.name}-{quality}.mkv")

    movie = make_movie(name="Dune")
    movie_repo = _IntegrityMovieRepo()
    movie_repo.add_movie(movie)
    movie_file = MovieFile(
        id=uuid.uuid4(),
        movie_id=movie.id,
        quality=Quality.uhd,
        variant="",
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        last_attempt_at=detected,
        sha1="def",
    )
    movie_repo.movie_files[movie_file.id] = movie_file

    # Wrap per-row lookups to prove they are unused for title resolution.
    show_repo.get_episode_calls = 0
    show_repo.get_season_calls = 0
    show_repo.get_show_calls = 0
    movie_repo.get_movie_calls = 0

    original_get_episode = show_repo.get_episode
    original_get_season = show_repo.get_season_by_episode
    original_get_show = show_repo.get_show_by_id
    original_get_movie = movie_repo.get_movie_by_id

    async def counting_get_episode(*, episode_id):
        show_repo.get_episode_calls += 1
        return await original_get_episode(episode_id=episode_id)

    async def counting_get_season(*, episode_id):
        show_repo.get_season_calls += 1
        return await original_get_season(episode_id=episode_id)

    async def counting_get_show(*, show_id):
        show_repo.get_show_calls += 1
        return await original_get_show(show_id=show_id)

    async def counting_get_movie(*, movie_id):
        movie_repo.get_movie_calls += 1
        return await original_get_movie(movie_id=movie_id)

    show_repo.get_episode = counting_get_episode  # type: ignore[method-assign]
    show_repo.get_season_by_episode = counting_get_season  # type: ignore[method-assign]
    show_repo.get_show_by_id = counting_get_show  # type: ignore[method-assign]
    movie_repo.get_movie_by_id = counting_get_movie  # type: ignore[method-assign]

    _patch_list_paths(
        monkeypatch,
        show_paths=path_by_id,
        movie_paths={movie_file.id: Path("/lib/Dune.mkv")},
    )
    svc = _torrent_service(show_repo, movie_repo)
    page = _run(
        svc.list_integrity_mismatches(
            offset=0,
            limit=INTEGRITY_MISMATCH_MAX_LIMIT,
            show_service=_show_service(show_repo, path_by_id),
            movie_service=_movie_service(
                movie_repo, {movie_file.id: Path("/lib/Dune.mkv")}
            ),
        )
    )
    rows = page.items

    assert len(rows) == 4
    show_rows = [r for r in rows if r.media_type == "show"]
    movie_rows = [r for r in rows if r.media_type == "movie"]
    assert len(show_rows) == 3
    assert len(movie_rows) == 1
    assert {r.media_title for r in show_rows} == {"Severance", "The Bear"}
    assert movie_rows[0].media_title == "Dune"
    assert movie_rows[0].path == "/lib/Dune.mkv"

    # One batch each for shows + movies; no per-row title lookups.
    assert getattr(show_repo, "context_batch_calls", 0) == 1
    assert getattr(movie_repo, "name_batch_calls", 0) == 1
    assert getattr(show_repo, "shows_by_ids_calls", 0) == 1
    assert getattr(movie_repo, "movies_by_ids_calls", 0) == 1
    assert show_repo.get_episode_calls == 0
    assert show_repo.get_season_calls == 0
    assert show_repo.get_show_calls == 0
    assert movie_repo.get_movie_calls == 0


def test_rebaseline_nulls_sha1_and_error_dismiss_keeps_sha1() -> None:
    show = make_show()
    episode = show.seasons[0].episodes[0]
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    file_id = uuid.uuid4()
    show_repo.episode_files[file_id] = EpisodeFile(
        id=file_id,
        episode_id=episode.id,
        quality=Quality.hd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
    )

    movie = make_movie()
    movie_repo = _IntegrityMovieRepo()
    movie_repo.add_movie(movie)
    movie_file_id = uuid.uuid4()
    movie_repo.movie_files[movie_file_id] = MovieFile(
        id=movie_file_id,
        movie_id=movie.id,
        quality=Quality.hd,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="def",
    )

    svc = _torrent_service(show_repo, movie_repo)
    show_svc = _show_service(show_repo, {})
    movie_svc = _movie_service(movie_repo, {})

    _run(
        svc.rebaseline_file(
            media_type=MediaType.show,
            file_id=file_id,
            show_service=show_svc,
            movie_service=movie_svc,
        )
    )
    cleared = show_repo.episode_files[file_id]
    assert cleared.import_error is None
    assert cleared.sha1 is None
    assert cleared.import_status == ImportOutcome.imported

    _run(
        svc.dismiss_mismatch(
            media_type=MediaType.movie,
            file_id=movie_file_id,
            show_service=show_svc,
            movie_service=movie_svc,
        )
    )
    dismissed = movie_repo.movie_files[movie_file_id]
    assert dismissed.import_error is None
    assert dismissed.sha1 == "def"
    assert dismissed.import_status == ImportOutcome.imported


def test_rebaseline_unknown_id_raises_not_found() -> None:
    import pytest

    svc = _torrent_service(_IntegrityShowRepo(), _IntegrityMovieRepo())
    with pytest.raises(NotFoundError):
        _run(
            svc.rebaseline_file(
                media_type=MediaType.show,
                file_id=uuid.uuid4(),
                show_service=_show_service(_IntegrityShowRepo(), {}),
                movie_service=_movie_service(_IntegrityMovieRepo(), {}),
            )
        )


@contextmanager
def integrity_client(
    *,
    superuser: bool = True,
    anonymous: bool = False,
    show_repo: _IntegrityShowRepo | None = None,
    movie_repo: _IntegrityMovieRepo | None = None,
) -> Generator[tuple[TestClient, _IntegrityShowRepo, _IntegrityMovieRepo]]:
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_service
    from miramedia.shows.dependencies import get_show_service
    from miramedia.torrents.dependencies import get_torrent_service

    s_repo = show_repo or _IntegrityShowRepo()
    m_repo = movie_repo or _IntegrityMovieRepo()
    torrent_svc = _torrent_service(s_repo, m_repo)
    show_svc = _show_service(s_repo, {})
    movie_svc = _movie_service(m_repo, {})

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        if anonymous:
            raise HTTPException(status_code=401, detail="Unauthorized")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = superuser
        return user

    async def _superuser() -> Any:
        if anonymous:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not superuser:
            raise HTTPException(status_code=403, detail="Forbidden")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_torrent_service] = lambda: torrent_svc
    app.dependency_overrides[get_show_service] = lambda: show_svc
    app.dependency_overrides[get_movie_service] = lambda: movie_svc
    try:
        client = TestClient(app, raise_server_exceptions=False)
        yield client, s_repo, m_repo
    finally:
        app.dependency_overrides.clear()


def test_list_endpoint_maps_rows() -> None:
    show = make_show(name="Severance", season_number=3, episode_number=7)
    episode = show.seasons[0].episodes[0]
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=None,
        variant="remux",
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
    )

    with integrity_client(show_repo=show_repo) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["limit"] == INTEGRITY_MISMATCH_DEFAULT_LIMIT
    assert body["offset"] == 0
    assert body["next_offset"] is None
    assert len(body["items"]) == 1
    assert body["items"][0]["media_title"] == "Severance"
    assert body["items"][0]["episode"] == "S03E07"
    assert body["items"][0]["variant_tag"] == "remux"
    assert body["items"][0]["file_id"] == str(fid)


def test_rebaseline_and_dismiss_endpoints() -> None:
    show = make_show()
    episode = show.seasons[0].episodes[0]
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.hd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
    )

    movie = make_movie()
    movie_repo = _IntegrityMovieRepo()
    movie_repo.add_movie(movie)
    mid = uuid.uuid4()
    movie_repo.movie_files[mid] = MovieFile(
        id=mid,
        movie_id=movie.id,
        quality=Quality.hd,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="def",
    )

    with integrity_client(show_repo=show_repo, movie_repo=movie_repo) as (client, _, _):
        r = client.post(f"{PREFIX}/integrity/show/{fid}/rebaseline")
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        assert show_repo.episode_files[fid].sha1 is None
        assert show_repo.episode_files[fid].import_error is None

        r = client.post(f"{PREFIX}/integrity/movie/{mid}/dismiss")
        assert r.status_code == 200, r.text
        assert movie_repo.movie_files[mid].sha1 == "def"
        assert movie_repo.movie_files[mid].import_error is None


def test_integrity_404_unknown_id() -> None:
    with integrity_client() as (client, _, _):
        r = client.post(f"{PREFIX}/integrity/show/{uuid.uuid4()}/rebaseline")
    assert r.status_code == 404


class _RacyDismissShowRepo(_IntegrityShowRepo):
    async def clear_file_integrity_state(
        self,
        file_id: uuid.UUID,
        *,
        expected_sha1: str | None,
        expected_import_error: str,
        reset_sha1: bool,
    ) -> bool:
        row = self.episode_files.get(file_id)
        if row is not None:
            self.episode_files[file_id] = row.model_copy(
                update={
                    "sha1": "cccccccccccccccccccccccccccccccccccccccc",
                    "import_error": "sha1 mismatch (expected c…, got d…)",
                }
            )
        return await super().clear_file_integrity_state(
            file_id,
            expected_sha1=expected_sha1,
            expected_import_error=expected_import_error,
            reset_sha1=reset_sha1,
        )


class _RacyDismissMovieRepo(_IntegrityMovieRepo):
    async def clear_file_integrity_state(
        self,
        file_id: uuid.UUID,
        *,
        expected_sha1: str | None,
        expected_import_error: str,
        reset_sha1: bool,
    ) -> bool:
        row = self.movie_files.get(file_id)
        if row is not None:
            self.movie_files[file_id] = row.model_copy(
                update={
                    "sha1": "cccccccccccccccccccccccccccccccccccccccc",
                    "import_error": "sha1 mismatch (expected c…, got d…)",
                }
            )
        return await super().clear_file_integrity_state(
            file_id,
            expected_sha1=expected_sha1,
            expected_import_error=expected_import_error,
            reset_sha1=reset_sha1,
        )


@pytest.mark.parametrize(
    ("media_type", "endpoint_action", "repo_cls"),
    [
        ("show", "dismiss", _RacyDismissShowRepo),
        ("show", "rebaseline", _RacyDismissShowRepo),
        ("movie", "dismiss", _RacyDismissMovieRepo),
        ("movie", "rebaseline", _RacyDismissMovieRepo),
    ],
)
def test_integrity_action_409_when_row_changes_after_read(
    media_type: str, endpoint_action: str, repo_cls
) -> None:
    sha1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    import_error = "sha1 mismatch (expected a…, got b…)"
    if media_type == "show":
        show = make_show()
        repo = repo_cls()
        repo.add_show(show)
        episode = show.seasons[0].episodes[0]
        fid = uuid.uuid4()
        repo.episode_files[fid] = EpisodeFile(
            id=fid,
            episode_id=episode.id,
            quality=Quality.hd,
            torrent_id=None,
            import_status=ImportOutcome.imported,
            import_error=import_error,
            sha1=sha1,
        )
        with integrity_client(show_repo=repo) as (client, _, _):
            r = client.post(f"{PREFIX}/integrity/show/{fid}/{endpoint_action}")
        assert r.status_code == 409, r.text
        assert (
            repo.episode_files[fid].import_error
            == "sha1 mismatch (expected c…, got d…)"
        )
        assert (
            repo.episode_files[fid].sha1 == "cccccccccccccccccccccccccccccccccccccccc"
        )
    else:
        movie = make_movie()
        repo = repo_cls()
        repo.add_movie(movie)
        mid = uuid.uuid4()
        repo.movie_files[mid] = MovieFile(
            id=mid,
            movie_id=movie.id,
            quality=Quality.hd,
            import_status=ImportOutcome.imported,
            import_error=import_error,
            sha1=sha1,
        )
        with integrity_client(movie_repo=repo) as (client, _, _):
            r = client.post(f"{PREFIX}/integrity/movie/{mid}/{endpoint_action}")
        assert r.status_code == 409, r.text
        assert (
            repo.movie_files[mid].import_error == "sha1 mismatch (expected c…, got d…)"
        )
        assert repo.movie_files[mid].sha1 == "cccccccccccccccccccccccccccccccccccccccc"


def test_integrity_requires_superuser() -> None:
    with integrity_client(anonymous=True) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches")
    assert r.status_code == 401

    with integrity_client(superuser=False) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches")
    assert r.status_code == 403


def test_integrity_mismatch_limit_constants() -> None:
    assert INTEGRITY_MISMATCH_DEFAULT_LIMIT == 50
    assert INTEGRITY_MISMATCH_MAX_LIMIT == 100


def _seed_mismatch_rows(
    show_repo: _IntegrityShowRepo,
    movie_repo: _IntegrityMovieRepo,
    *,
    show_count: int,
    movie_count: int,
) -> None:
    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    for i in range(show_count):
        show = make_show(name=f"Show-{i}", season_number=1, episode_number=i + 1)
        show_repo.add_show(show)
        episode = show.seasons[0].episodes[0]
        fid = uuid.UUID(int=i + 1)
        show_repo.episode_files[fid] = EpisodeFile(
            id=fid,
            episode_id=episode.id,
            quality=Quality.hd,
            torrent_id=None,
            import_status=ImportOutcome.imported,
            import_error="sha1 mismatch (expected a…, got b…)",
            last_attempt_at=detected,
            sha1="abc",
        )
    for j in range(movie_count):
        movie = make_movie(name=f"Movie-{j}")
        movie_repo.add_movie(movie)
        mid = uuid.UUID(int=1000 + j + 1)
        movie_repo.movie_files[mid] = MovieFile(
            id=mid,
            movie_id=movie.id,
            quality=Quality.hd,
            import_status=ImportOutcome.imported,
            import_error="sha1 mismatch (expected a…, got b…)",
            last_attempt_at=detected,
            sha1="def",
        )


def test_integrity_mismatches_default_limit() -> None:
    show_repo = _IntegrityShowRepo()
    movie_repo = _IntegrityMovieRepo()
    _seed_mismatch_rows(show_repo, movie_repo, show_count=60, movie_count=0)
    with integrity_client(show_repo=show_repo, movie_repo=movie_repo) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limit"] == INTEGRITY_MISMATCH_DEFAULT_LIMIT
    assert len(body["items"]) == INTEGRITY_MISMATCH_DEFAULT_LIMIT
    assert body["total"] == 60
    assert body["next_offset"] == INTEGRITY_MISMATCH_DEFAULT_LIMIT


def test_integrity_mismatches_custom_limit_and_next_offset() -> None:
    show_repo = _IntegrityShowRepo()
    movie_repo = _IntegrityMovieRepo()
    _seed_mismatch_rows(show_repo, movie_repo, show_count=3, movie_count=2)
    with integrity_client(show_repo=show_repo, movie_repo=movie_repo) as (client, _, _):
        r = client.get(
            f"{PREFIX}/integrity/mismatches", params={"offset": 2, "limit": 3}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert body["offset"] == 2
    assert body["limit"] == 3
    assert len(body["items"]) == 3
    assert body["items"][0]["media_type"] == "show"
    assert body["items"][1]["media_type"] == "movie"
    assert body["items"][2]["media_type"] == "movie"
    assert body["next_offset"] is None


def test_integrity_mismatches_max_limit() -> None:
    show_repo = _IntegrityShowRepo()
    movie_repo = _IntegrityMovieRepo()
    _seed_mismatch_rows(show_repo, movie_repo, show_count=10, movie_count=0)
    with integrity_client(show_repo=show_repo, movie_repo=movie_repo) as (client, _, _):
        r = client.get(
            f"{PREFIX}/integrity/mismatches",
            params={"limit": INTEGRITY_MISMATCH_MAX_LIMIT},
        )
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 10


def test_integrity_mismatches_over_max_limit_rejected() -> None:
    with integrity_client() as (client, _, _):
        r = client.get(
            f"{PREFIX}/integrity/mismatches",
            params={"limit": INTEGRITY_MISMATCH_MAX_LIMIT + 1},
        )
    assert r.status_code == 422


def test_integrity_mismatches_empty_page_past_total() -> None:
    show_repo = _IntegrityShowRepo()
    movie_repo = _IntegrityMovieRepo()
    _seed_mismatch_rows(show_repo, movie_repo, show_count=2, movie_count=1)
    with integrity_client(show_repo=show_repo, movie_repo=movie_repo) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches", params={"offset": 99})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 3
    assert body["next_offset"] is None


def test_integrity_mismatches_hard_cap_materializes_at_most_max() -> None:
    show_repo = _IntegrityShowRepo()
    movie_repo = _IntegrityMovieRepo()
    _seed_mismatch_rows(show_repo, movie_repo, show_count=80, movie_count=80)
    with integrity_client(show_repo=show_repo, movie_repo=movie_repo) as (client, _, _):
        r = client.get(
            f"{PREFIX}/integrity/mismatches",
            params={"limit": INTEGRITY_MISMATCH_MAX_LIMIT},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 160
    assert len(body["items"]) == INTEGRITY_MISMATCH_MAX_LIMIT
    assert body["next_offset"] == INTEGRITY_MISMATCH_MAX_LIMIT


def test_list_integrity_mismatches_next_offset_uses_page_span_after_dismiss() -> None:
    """Dismiss between key snapshot and row fetch must not stall pagination."""
    from miramedia.torrents.integrity import Sha1MismatchPage, Sha1MismatchPageKey

    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    dismissed_id = uuid.UUID(int=2)

    class _DismissBetweenFetchRepo(_IntegrityShowRepo):
        async def get_sha1_mismatch_episode_files_by_ids(
            self, file_ids: list[uuid.UUID]
        ) -> dict[uuid.UUID, EpisodeFile]:
            filtered = [fid for fid in file_ids if fid != dismissed_id]
            return await super().get_sha1_mismatch_episode_files_by_ids(filtered)

    show_repo = _DismissBetweenFetchRepo()
    movie_repo = _IntegrityMovieRepo()
    for i in range(3):
        show = make_show(name=f"Show-{i}", season_number=1, episode_number=i + 1)
        show_repo.add_show(show)
        episode = show.seasons[0].episodes[0]
        fid = uuid.UUID(int=i + 1)
        show_repo.episode_files[fid] = EpisodeFile(
            id=fid,
            episode_id=episode.id,
            quality=Quality.hd,
            torrent_id=None,
            import_status=ImportOutcome.imported,
            import_error="sha1 mismatch (expected a…, got b…)",
            last_attempt_at=detected,
            sha1="abc",
        )

    class _FixedPageTorrentRepo(FakeTorrentRepository):
        async def paginate_sha1_mismatch_keys(self, *, offset: int, limit: int):
            keys = [
                Sha1MismatchPageKey("show", uuid.UUID(int=1)),
                Sha1MismatchPageKey("show", dismissed_id),
                Sha1MismatchPageKey("show", uuid.UUID(int=3)),
            ]
            return Sha1MismatchPage(keys=keys[offset : offset + limit], total=5)

    torrent_repo = _FixedPageTorrentRepo(show_repo=show_repo, movie_repo=movie_repo)
    svc = TorrentService(torrent_repository=torrent_repo)  # type: ignore[arg-type]
    page = _run(
        svc.list_integrity_mismatches(
            offset=0,
            limit=3,
            show_service=_show_service(show_repo, {}),
            movie_service=_movie_service(movie_repo, {}),
        )
    )

    assert len(page.items) == 2
    assert page.total == 5
    assert page.next_offset == 3
    assert page.next_offset != len(page.items)


def test_list_integrity_mismatches_releases_all_repository_sessions(
    monkeypatch,
) -> None:
    show = make_show(name="Severance", season_number=1, episode_number=1)
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    show_repo.db = FakeDb()
    episode = show.seasons[0].episodes[0]
    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.hd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        last_attempt_at=detected,
        sha1="abc",
    )
    movie_repo = _IntegrityMovieRepo()
    movie_repo.db = FakeDb()
    released_ids: list[int] = []
    queries_after_release = 0

    async def _tracking_release(*sessions: Any) -> None:
        released_ids.extend(id(session) for session in sessions)

    async def _slow_episode_paths(rows, episode_context, shows, layout):  # noqa: ARG001
        nonlocal queries_after_release
        if queries_after_release:
            msg = "repository query after session release"
            raise AssertionError(msg)
        await asyncio.sleep(0.01)
        return {row.id: None for row in rows}

    original_get_by_ids = show_repo.get_sha1_mismatch_episode_files_by_ids

    async def _counting_get_by_ids(file_ids):
        nonlocal queries_after_release
        if released_ids:
            queries_after_release += 1
        return await original_get_by_ids(file_ids)

    show_repo.get_sha1_mismatch_episode_files_by_ids = _counting_get_by_ids  # type: ignore[method-assign]

    monkeypatch.setattr(
        "miramedia.database.release_sessions_before_external_io",
        _tracking_release,
    )
    monkeypatch.setattr(
        "miramedia.torrents.service.batch_resolve_episode_paths_async",
        _slow_episode_paths,
    )
    monkeypatch.setattr(
        "miramedia.torrents.service.batch_resolve_movie_paths_async",
        _async_empty_movie_paths,
    )

    svc = _torrent_service(show_repo, movie_repo)
    page = _run(
        svc.list_integrity_mismatches(
            offset=0,
            limit=INTEGRITY_MISMATCH_MAX_LIMIT,
            show_service=_show_service(show_repo, {}),
            movie_service=_movie_service(movie_repo, {}),
        )
    )

    assert len(page.items) == 1
    assert len(released_ids) == 3
    assert len(set(released_ids)) == 3
    assert queries_after_release == 0


def test_list_integrity_mismatches_releases_session_before_directory_scan(
    monkeypatch,
) -> None:
    show = make_show(name="Severance", season_number=1, episode_number=1)
    show_repo = _IntegrityShowRepo()
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.hd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        last_attempt_at=detected,
        sha1="abc",
    )
    movie_repo = _IntegrityMovieRepo()
    movie_repo.db = FakeDb()
    sessions_open: list[bool] = []
    released_before_scan = False

    @asynccontextmanager
    async def _tracking_background_session():
        sessions_open.append(True)
        bg_session, _ = background_session_factory(episode_rows=[])
        async with bg_session() as session:
            try:
                yield session
            finally:
                sessions_open.pop()

    async def _tracking_release(*_sessions: Any) -> None:
        nonlocal released_before_scan
        released_before_scan = True

    async def _slow_episode_paths(rows, episode_context, shows, layout):  # noqa: ARG001
        assert released_before_scan, "session must be released before directory scan"
        assert sessions_open == [], "background session must not be open during scan"
        await asyncio.sleep(0.01)
        return {row.id: None for row in rows}

    monkeypatch.setattr(
        "miramedia.database.release_sessions_before_external_io",
        _tracking_release,
    )
    monkeypatch.setattr(
        "miramedia.database.background_session",
        _tracking_background_session,
    )
    monkeypatch.setattr(
        "miramedia.torrents.service.batch_resolve_episode_paths_async",
        _slow_episode_paths,
    )
    monkeypatch.setattr(
        "miramedia.torrents.service.batch_resolve_movie_paths_async",
        _async_empty_movie_paths,
    )

    svc = _torrent_service(show_repo, movie_repo)
    page = _run(
        svc.list_integrity_mismatches(
            offset=0,
            limit=INTEGRITY_MISMATCH_MAX_LIMIT,
            show_service=_show_service(show_repo, {}),
            movie_service=_movie_service(movie_repo, {}),
        )
    )

    assert len(page.items) == 1
    assert released_before_scan is True


async def _async_empty_movie_paths(*_args, **_kwargs) -> dict:
    return {}

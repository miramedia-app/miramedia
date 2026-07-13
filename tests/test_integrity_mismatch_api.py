"""DB-free tests for the SHA1 integrity-mismatch API."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.exceptions import NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieFile
from miramedia.shows.schemas import EpisodeFile
from miramedia.torrents.schemas import IntegrityMismatch, MediaType, Quality
from miramedia.torrents.service import TorrentService
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
    make_movie,
    make_show,
)

PREFIX = "/api/v1/torrents"


def _run(coro):
    return asyncio.run(coro)


class _IntegrityShowRepo(FakeShowRepository):
    async def list_sha1_mismatch_files(self) -> list[EpisodeFile]:
        return [
            f
            for f in self.episode_files.values()
            if f.import_status == ImportOutcome.imported
            and (f.import_error or "").startswith("sha1 mismatch")
        ]

    async def clear_file_integrity_state(
        self, file_id: uuid.UUID, *, reset_sha1: bool
    ) -> bool:
        row = self.episode_files.get(file_id)
        if row is None:
            return False
        update: dict[str, Any] = {"import_error": None}
        if reset_sha1:
            update["sha1"] = None
        self.episode_files[file_id] = row.model_copy(update=update)
        return True


class _IntegrityMovieRepo(FakeMovieRepository):
    async def list_sha1_mismatch_files(self) -> list[MovieFile]:
        return [
            f
            for f in self.movie_files.values()
            if f.import_status == ImportOutcome.imported
            and (f.import_error or "").startswith("sha1 mismatch")
        ]

    async def clear_file_integrity_state(
        self, file_id: uuid.UUID, *, reset_sha1: bool
    ) -> bool:
        row = self.movie_files.get(file_id)
        if row is None:
            return False
        update: dict[str, Any] = {"import_error": None}
        if reset_sha1:
            update["sha1"] = None
        self.movie_files[file_id] = row.model_copy(update=update)
        return True


def _show_service(repo: _IntegrityShowRepo, path_by_id: dict[uuid.UUID, Path | None]):
    async def resolve_episode_file_path(row: EpisodeFile) -> Path | None:
        return path_by_id.get(row.id)

    return MagicMock(
        show_repository=repo, resolve_episode_file_path=resolve_episode_file_path
    )


def _movie_service(repo: _IntegrityMovieRepo, path_by_id: dict[uuid.UUID, Path | None]):
    async def resolve_movie_file_path(row: MovieFile) -> Path | None:
        return path_by_id.get(row.id)

    return MagicMock(
        movie_repository=repo, resolve_movie_file_path=resolve_movie_file_path
    )


def test_list_integrity_mismatches_maps_show_and_movie_shape() -> None:
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

    svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
    rows = _run(
        svc.list_integrity_mismatches(
            show_service=_show_service(
                show_repo, {ep_file.id: Path("/lib/S03E07.mkv")}
            ),
            movie_service=_movie_service(movie_repo, {movie_file.id: None}),
        )
    )

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


def test_list_integrity_mismatches_batches_lookups() -> None:
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

    svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
    rows = _run(
        svc.list_integrity_mismatches(
            show_service=_show_service(show_repo, path_by_id),
            movie_service=_movie_service(
                movie_repo, {movie_file.id: Path("/lib/Dune.mkv")}
            ),
        )
    )

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

    svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
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

    svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
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
    torrent_svc = TorrentService(torrent_repository=FakeTorrentRepository())  # type: ignore[arg-type]
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
    assert len(body) == 1
    assert body[0]["media_title"] == "Severance"
    assert body[0]["episode"] == "S03E07"
    assert body[0]["variant_tag"] == "remux"
    assert body[0]["file_id"] == str(fid)


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


def test_integrity_requires_superuser() -> None:
    with integrity_client(anonymous=True) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches")
    assert r.status_code == 401

    with integrity_client(superuser=False) as (client, _, _):
        r = client.get(f"{PREFIX}/integrity/mismatches")
    assert r.status_code == 403

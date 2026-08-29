"""Regression tests: stream file lookups must match route parent media IDs."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.config import MiraMediaConfig
from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieFile, MovieId
from miramedia.shows.schemas import EpisodeFile, EpisodeId
from miramedia.torrents.schemas import Quality
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    make_movie,
    make_show,
)

PREFIX = "/api/v1/streams"
NOT_FOUND_EPISODE = "Episode file not found"
NOT_FOUND_MOVIE = "Movie file not found"
SAFE_NOT_FOUND = "Not Found"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@dataclass
class StreamResolutionSpies:
    episode_video: AsyncMock
    movie_video: AsyncMock
    subtitle: AsyncMock
    hls_playlist: AsyncMock
    serve_file: MagicMock
    direct_play_probe: MagicMock


@contextmanager
def stream_resolution_spies(
    tmp_path: Path | None = None,
) -> Iterator[StreamResolutionSpies]:
    from miramedia.streams.router import _serve_file as real_serve_file

    with (
        patch(
            "miramedia.streams.router._resolve_episode_video_file",
            new_callable=AsyncMock,
        ) as episode_video,
        patch(
            "miramedia.streams.router._resolve_movie_video_file",
            new_callable=AsyncMock,
        ) as movie_video,
        patch(
            "miramedia.streams.router._resolve_subtitle_file",
            new_callable=AsyncMock,
        ) as subtitle,
        patch(
            "miramedia.streams.router.ensure_hls_playlist",
            new_callable=AsyncMock,
        ) as hls_playlist,
        patch("miramedia.streams.router._serve_file") as serve_file,
        patch("miramedia.streams.router.can_direct_play") as direct_play_probe,
    ):
        if tmp_path is not None:
            video_file = tmp_path / "media.mkv"
            video_file.write_bytes(b"video")
            subtitle_file = tmp_path / "sub.en.vtt"
            subtitle_file.write_text("WEBVTT\n", encoding="utf-8")
            episode_video.return_value = video_file
            movie_video.return_value = video_file
            subtitle.return_value = subtitle_file
            serve_file.side_effect = real_serve_file
        yield StreamResolutionSpies(
            episode_video=episode_video,
            movie_video=movie_video,
            subtitle=subtitle,
            hls_playlist=hls_playlist,
            serve_file=serve_file,
            direct_play_probe=direct_play_probe,
        )


def _assert_no_downstream_resolution(spies: StreamResolutionSpies) -> None:
    spies.episode_video.assert_not_called()
    spies.movie_video.assert_not_called()
    spies.subtitle.assert_not_called()
    spies.hls_playlist.assert_not_called()
    spies.serve_file.assert_not_called()
    spies.direct_play_probe.assert_not_called()


@pytest.fixture
def episode_binding() -> tuple[EpisodeId, EpisodeId, EpisodeFile]:
    show = make_show(name="Bound Show", season_number=1, episode_number=1)
    other = make_show(name="Other Show", season_number=1, episode_number=1)
    episode_id = show.seasons[0].episodes[0].id
    other_episode_id = other.seasons[0].episodes[0].id
    ep_file = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=episode_id,
        quality=Quality.fullhd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
    )
    return episode_id, other_episode_id, ep_file


@pytest.fixture
def movie_binding() -> tuple[MovieId, MovieId, MovieFile]:
    movie = make_movie(name="Bound Movie")
    other = make_movie(name="Other Movie")
    mov_file = MovieFile(
        id=uuid.uuid4(),
        movie_id=movie.id,
        quality=Quality.fullhd,
        import_status=ImportOutcome.imported,
    )
    return movie.id, other.id, mov_file


def test_load_episode_file_accepts_matching_pair(
    episode_binding: tuple[EpisodeId, EpisodeId, EpisodeFile],
) -> None:
    from miramedia.streams.router import _load_episode_file

    episode_id, _, ep_file = episode_binding
    show_service = MagicMock()
    show_service.show_repository.get_episode_file_by_id = AsyncMock(
        return_value=ep_file
    )

    loaded = _run(
        _load_episode_file(
            show_service=show_service,
            episode_id=episode_id,
            file_id=ep_file.id,
        )
    )
    assert loaded is ep_file


def test_load_episode_file_rejects_mismatched_episode_id(
    episode_binding: tuple[EpisodeId, EpisodeId, EpisodeFile],
) -> None:
    from miramedia.streams.router import _load_episode_file

    _, other_episode_id, ep_file = episode_binding
    show_service = MagicMock()
    show_service.show_repository.get_episode_file_by_id = AsyncMock(
        return_value=ep_file
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _load_episode_file(
                show_service=show_service,
                episode_id=other_episode_id,
                file_id=ep_file.id,
            )
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == NOT_FOUND_EPISODE


def test_load_episode_file_rejects_missing_file(
    episode_binding: tuple[EpisodeId, EpisodeId, EpisodeFile],
) -> None:
    from miramedia.streams.router import _load_episode_file

    episode_id, _, ep_file = episode_binding
    show_service = MagicMock()
    show_service.show_repository.get_episode_file_by_id = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _load_episode_file(
                show_service=show_service,
                episode_id=episode_id,
                file_id=ep_file.id,
            )
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == NOT_FOUND_EPISODE


def test_load_movie_file_accepts_matching_pair(
    movie_binding: tuple[MovieId, MovieId, MovieFile],
) -> None:
    from miramedia.streams.router import _load_movie_file

    movie_id, _, mov_file = movie_binding
    movie_service = MagicMock()
    movie_service.movie_repository.get_movie_file_by_id = AsyncMock(
        return_value=mov_file
    )

    loaded = _run(
        _load_movie_file(
            movie_service=movie_service,
            movie_id=movie_id,
            file_id=mov_file.id,
        )
    )
    assert loaded is mov_file


def test_load_movie_file_rejects_mismatched_movie_id(
    movie_binding: tuple[MovieId, MovieId, MovieFile],
) -> None:
    from miramedia.streams.router import _load_movie_file

    _, other_movie_id, mov_file = movie_binding
    movie_service = MagicMock()
    movie_service.movie_repository.get_movie_file_by_id = AsyncMock(
        return_value=mov_file
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _load_movie_file(
                movie_service=movie_service,
                movie_id=other_movie_id,
                file_id=mov_file.id,
            )
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == NOT_FOUND_MOVIE


def test_load_movie_file_rejects_missing_file(
    movie_binding: tuple[MovieId, MovieId, MovieFile],
) -> None:
    from miramedia.streams.router import _load_movie_file

    movie_id, _, mov_file = movie_binding
    movie_service = MagicMock()
    movie_service.movie_repository.get_movie_file_by_id = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _load_movie_file(
                movie_service=movie_service,
                movie_id=movie_id,
                file_id=mov_file.id,
            )
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == NOT_FOUND_MOVIE


@contextmanager
def stream_client(
    *,
    show_repo: FakeShowRepository | None = None,
    movie_repo: FakeMovieRepository | None = None,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.movies.service import MovieService
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.shows.service import ShowService

    fake_show_repo = show_repo or FakeShowRepository()
    fake_movie_repo = movie_repo or FakeMovieRepository()

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        return user

    def _show_repo_dep() -> FakeShowRepository:
        return fake_show_repo

    def _movie_repo_dep() -> FakeMovieRepository:
        return fake_movie_repo

    def _show_service_dep() -> ShowService:
        return ShowService(
            show_repository=fake_show_repo,  # type: ignore[arg-type]
            torrent_service=MagicMock(),
            indexer_service=MagicMock(),
            notification_service=MagicMock(),
        )

    def _movie_service_dep() -> MovieService:
        return MovieService(
            movie_repository=fake_movie_repo,  # type: ignore[arg-type]
            torrent_service=MagicMock(),
            indexer_service=MagicMock(),
            notification_service=MagicMock(),
        )

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[get_show_repository] = _show_repo_dep
    app.dependency_overrides[get_movie_repository] = _movie_repo_dep
    app.dependency_overrides[get_show_service] = _show_service_dep
    app.dependency_overrides[get_movie_service] = _movie_service_dep
    try:
        client = TestClient(app, raise_server_exceptions=False)
        yield client
    finally:
        app.dependency_overrides.clear()


def _episode_mismatch_routes(episode_id: EpisodeId, file_id: uuid.UUID) -> list[str]:
    return [
        f"{PREFIX}/episodes/{episode_id}?file_id={file_id}",
        f"{PREFIX}/episodes/{episode_id}?file_id={file_id}&download=true",
        f"{PREFIX}/episodes/{episode_id}/probe?file_id={file_id}",
        f"{PREFIX}/episodes/{episode_id}/hls/index.m3u8?file_id={file_id}",
        f"{PREFIX}/episodes/{episode_id}/hls/seg000.ts?file_id={file_id}",
        f"{PREFIX}/subtitles/episodes/{episode_id}/en?file_id={file_id}",
    ]


def _movie_mismatch_routes(movie_id: MovieId, file_id: uuid.UUID) -> list[str]:
    return [
        f"{PREFIX}/movies/{movie_id}?file_id={file_id}",
        f"{PREFIX}/movies/{movie_id}?file_id={file_id}&download=true",
        f"{PREFIX}/movies/{movie_id}/probe?file_id={file_id}",
        f"{PREFIX}/movies/{movie_id}/hls/index.m3u8?file_id={file_id}",
        f"{PREFIX}/movies/{movie_id}/hls/seg000.ts?file_id={file_id}",
        f"{PREFIX}/subtitles/movies/{movie_id}/en?file_id={file_id}",
    ]


def _seed_episode_repo(
    episode_id: EpisodeId,
    other_episode_id: EpisodeId,
    ep_file: EpisodeFile,
) -> FakeShowRepository:
    show_repo = FakeShowRepository()
    show = make_show(name="Route Show", season_number=1, episode_number=1)
    show.seasons[0].episodes[0].id = episode_id
    show_repo.add_show(show)
    other_show = make_show(name="Other Route Show", season_number=1, episode_number=1)
    other_show.seasons[0].episodes[0].id = other_episode_id
    show_repo.add_show(other_show)
    show_repo.episode_files[ep_file.id] = ep_file
    return show_repo


def _seed_movie_repo(
    movie_id: MovieId,
    other_movie_id: MovieId,
    mov_file: MovieFile,
) -> FakeMovieRepository:
    movie_repo = FakeMovieRepository()
    movie = make_movie(name="Route Movie")
    movie.id = movie_id
    movie_repo.add_movie(movie)
    other_movie = make_movie(name="Other Route Movie")
    other_movie.id = other_movie_id
    movie_repo.add_movie(other_movie)
    movie_repo.movie_files[mov_file.id] = mov_file
    return movie_repo


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("episode", id="episode"),
        pytest.param("movie", id="movie"),
    ],
)
def test_routes_reject_mismatched_file_id_before_resolution(
    path: str,
    episode_binding: tuple[EpisodeId, EpisodeId, EpisodeFile],
    movie_binding: tuple[MovieId, MovieId, MovieFile],
) -> None:
    if path == "episode":
        episode_id, other_episode_id, media_file = episode_binding
        show_repo = _seed_episode_repo(episode_id, other_episode_id, media_file)
        routes = _episode_mismatch_routes(other_episode_id, media_file.id)
        client_ctx = stream_client(show_repo=show_repo)
    else:
        movie_id, other_movie_id, media_file = movie_binding
        movie_repo = _seed_movie_repo(movie_id, other_movie_id, media_file)
        routes = _movie_mismatch_routes(other_movie_id, media_file.id)
        client_ctx = stream_client(movie_repo=movie_repo)

    with client_ctx as client, stream_resolution_spies() as spies:
        for route in routes:
            response = client.get(route)
            assert response.status_code == 404, route
            assert response.text == SAFE_NOT_FOUND
        _assert_no_downstream_resolution(spies)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("episode", id="episode"),
        pytest.param("movie", id="movie"),
    ],
)
def test_routes_missing_and_mismatched_file_share_safe_404(
    path: str,
    episode_binding: tuple[EpisodeId, EpisodeId, EpisodeFile],
    movie_binding: tuple[MovieId, MovieId, MovieFile],
) -> None:
    missing_file_id = uuid.uuid4()
    if path == "episode":
        episode_id, other_episode_id, ep_file = episode_binding
        show_repo = _seed_episode_repo(episode_id, other_episode_id, ep_file)
        probe_route = _episode_mismatch_routes(other_episode_id, ep_file.id)[2]
        missing_route = _episode_mismatch_routes(other_episode_id, missing_file_id)[2]
        client_ctx = stream_client(show_repo=show_repo)
    else:
        movie_id, other_movie_id, mov_file = movie_binding
        movie_repo = _seed_movie_repo(movie_id, other_movie_id, mov_file)
        probe_route = _movie_mismatch_routes(other_movie_id, mov_file.id)[2]
        missing_route = _movie_mismatch_routes(other_movie_id, missing_file_id)[2]
        client_ctx = stream_client(movie_repo=movie_repo)

    with client_ctx as client, stream_resolution_spies() as spies:
        missing = client.get(missing_route)
        mismatch = client.get(probe_route)

    assert missing.status_code == mismatch.status_code == 404
    assert missing.text == mismatch.text == SAFE_NOT_FOUND
    _assert_no_downstream_resolution(spies)


def test_cross_media_type_file_id_is_not_found(
    episode_binding: tuple[EpisodeId, EpisodeId, EpisodeFile],
    movie_binding: tuple[MovieId, MovieId, MovieFile],
) -> None:
    episode_id, _, ep_file = episode_binding
    movie_id, _, mov_file = movie_binding

    show_repo = FakeShowRepository()
    show = make_show(name="Cross Show", season_number=1, episode_number=1)
    show.seasons[0].episodes[0].id = episode_id
    show_repo.add_show(show)
    show_repo.episode_files[ep_file.id] = ep_file

    movie_repo = FakeMovieRepository()
    movie = make_movie(name="Cross Movie")
    movie.id = movie_id
    movie_repo.add_movie(movie)
    movie_repo.movie_files[mov_file.id] = mov_file

    with (
        stream_client(show_repo=show_repo, movie_repo=movie_repo) as client,
        stream_resolution_spies() as spies,
    ):
        movie_response = client.get(f"{PREFIX}/movies/{movie_id}?file_id={ep_file.id}")
        episode_response = client.get(
            f"{PREFIX}/episodes/{episode_id}?file_id={mov_file.id}"
        )

    assert movie_response.status_code == episode_response.status_code == 404
    assert movie_response.text == episode_response.text == SAFE_NOT_FOUND
    _assert_no_downstream_resolution(spies)


STREAM_FLAG_MATRIX = (
    pytest.param(False, False, id="both-off"),
    pytest.param(True, False, id="streaming-only"),
    pytest.param(False, True, id="downloads-only"),
    pytest.param(True, True, id="both-on"),
)


def _set_stream_flags(*, enabled: bool, downloads: bool) -> None:
    streams = MiraMediaConfig().streams
    streams.enabled = enabled
    streams.downloads = downloads


def _media_stream_expects_503(enabled: bool) -> bool:
    return not enabled


def _media_download_expects_503(*, downloads: bool) -> bool:
    return not downloads


def _subtitle_expects_503(*, enabled: bool, downloads: bool) -> bool:
    return not enabled and not downloads


@contextmanager
def gated_stream_client(
    override_dependency: Callable[[Callable[..., object], object], None],
    *,
    show_repo: FakeShowRepository | None = None,
    movie_repo: FakeMovieRepository | None = None,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.movies.service import MovieService
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.shows.service import ShowService

    fake_show_repo = show_repo or FakeShowRepository()
    fake_movie_repo = movie_repo or FakeMovieRepository()

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        return user

    def _show_repo_dep() -> FakeShowRepository:
        return fake_show_repo

    def _movie_repo_dep() -> FakeMovieRepository:
        return fake_movie_repo

    def _show_service_dep() -> ShowService:
        return ShowService(
            show_repository=fake_show_repo,  # type: ignore[arg-type]
            torrent_service=MagicMock(),
            indexer_service=MagicMock(),
            notification_service=MagicMock(),
        )

    def _movie_service_dep() -> MovieService:
        return MovieService(
            movie_repository=fake_movie_repo,  # type: ignore[arg-type]
            torrent_service=MagicMock(),
            indexer_service=MagicMock(),
            notification_service=MagicMock(),
        )

    override_dependency(get_session, _stub_session)
    override_dependency(current_active_user, _active_user)
    override_dependency(get_show_repository, _show_repo_dep)
    override_dependency(get_movie_repository, _movie_repo_dep)
    override_dependency(get_show_service, _show_service_dep)
    override_dependency(get_movie_service, _movie_service_dep)
    yield TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(("enabled", "downloads"), STREAM_FLAG_MATRIX)
def test_stream_access_gate_matrix_movie(
    enabled: bool,
    downloads: bool,
    movie_binding: tuple[MovieId, MovieId, MovieFile],
    override_dependency: Callable[[Callable[..., object], object], None],
    tmp_path: Path,
) -> None:
    _set_stream_flags(enabled=enabled, downloads=downloads)
    movie_id, other_movie_id, mov_file = movie_binding
    movie_repo = _seed_movie_repo(movie_id, other_movie_id, mov_file)
    stream_url = f"{PREFIX}/movies/{movie_id}?file_id={mov_file.id}"
    download_url = f"{stream_url}&download=true"
    subtitle_url = f"{PREFIX}/subtitles/movies/{movie_id}/en?file_id={mov_file.id}"

    cases = (
        (stream_url, _media_stream_expects_503(enabled)),
        (download_url, _media_download_expects_503(downloads=downloads)),
        (subtitle_url, _subtitle_expects_503(enabled=enabled, downloads=downloads)),
    )
    for url, expects_503 in cases:
        with (
            gated_stream_client(override_dependency, movie_repo=movie_repo) as client,
            stream_resolution_spies(tmp_path) as spies,
        ):
            response = client.get(url)
        if expects_503:
            assert response.status_code == 503, url
            _assert_no_downstream_resolution(spies)
        elif url == download_url:
            assert response.status_code == 200, url
            disposition = response.headers.get("content-disposition", "")
            assert disposition.startswith('attachment; filename="')
            assert disposition.endswith('media.mkv"')
        elif url == subtitle_url:
            assert response.status_code == 200, url
            assert response.headers.get("content-type", "").startswith("text/")
        else:
            assert response.status_code == 200, url
            disposition = response.headers.get("content-disposition", "")
            assert not disposition.startswith("attachment; filename=")


def test_media_stream_gate_requires_in_body_enabled_check(
    movie_binding: tuple[MovieId, MovieId, MovieFile],
    override_dependency: Callable[[Callable[..., object], object], None],
    tmp_path: Path,
) -> None:
    """Fails if the movie handler drops require_stream_or_download_enabled(False)."""
    _set_stream_flags(enabled=False, downloads=True)
    movie_id, other_movie_id, mov_file = movie_binding
    movie_repo = _seed_movie_repo(movie_id, other_movie_id, mov_file)
    video_file = tmp_path / "movie.mkv"
    video_file.write_bytes(b"video")
    stream_url = f"{PREFIX}/movies/{movie_id}?file_id={mov_file.id}"

    with (
        gated_stream_client(override_dependency, movie_repo=movie_repo) as client,
        patch(
            "miramedia.streams.router._resolve_movie_video_file",
            new_callable=AsyncMock,
            return_value=video_file,
        ),
        patch("miramedia.streams.router._serve_file") as serve_file,
    ):
        response = client.get(stream_url)

    assert response.status_code == 503
    serve_file.assert_not_called()


def test_movie_download_sets_attachment_disposition(
    movie_binding: tuple[MovieId, MovieId, MovieFile],
    override_dependency: Callable[[Callable[..., object], object], None],
    tmp_path: Path,
) -> None:
    _set_stream_flags(enabled=False, downloads=True)
    movie_id, other_movie_id, mov_file = movie_binding
    movie_repo = _seed_movie_repo(movie_id, other_movie_id, mov_file)
    video_file = tmp_path / "movie.mkv"
    video_file.write_bytes(b"video")
    download_url = f"{PREFIX}/movies/{movie_id}?file_id={mov_file.id}&download=true"

    with (
        gated_stream_client(override_dependency, movie_repo=movie_repo) as client,
        patch(
            "miramedia.streams.router._resolve_movie_video_file",
            new_callable=AsyncMock,
            return_value=video_file,
        ),
    ):
        response = client.get(download_url)

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert disposition.startswith('attachment; filename="')
    assert disposition.endswith('movie.mkv"')


def test_movie_stream_has_no_attachment_disposition(
    movie_binding: tuple[MovieId, MovieId, MovieFile],
    override_dependency: Callable[[Callable[..., object], object], None],
    tmp_path: Path,
) -> None:
    _set_stream_flags(enabled=True, downloads=False)
    movie_id, other_movie_id, mov_file = movie_binding
    movie_repo = _seed_movie_repo(movie_id, other_movie_id, mov_file)
    video_file = tmp_path / "movie.mkv"
    video_file.write_bytes(b"video")
    stream_url = f"{PREFIX}/movies/{movie_id}?file_id={mov_file.id}"

    with (
        gated_stream_client(override_dependency, movie_repo=movie_repo) as client,
        patch(
            "miramedia.streams.router._resolve_movie_video_file",
            new_callable=AsyncMock,
            return_value=video_file,
        ),
    ):
        response = client.get(stream_url)

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert not disposition.startswith("attachment; filename=")

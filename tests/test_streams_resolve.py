"""Regression: stream resolve helpers always validate paths (cached or miss)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from miramedia.exceptions import NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieFile
from miramedia.shows.schemas import EpisodeFile, EpisodeId
from miramedia.streams.router import (
    _resolve_episode_video_file,
    _resolve_movie_video_file,
    _resolve_subtitle_file,
    _resolve_video_file,
    _validate_media_path,
)
from miramedia.torrents.schemas import Quality
from tests.fakes.repositories import FakeMovieRepository, make_movie


def _run(coro):
    return asyncio.run(coro)


def _config_stub(tmp_path: Path) -> SimpleNamespace:
    show_root = tmp_path / "shows"
    movie_root = tmp_path / "movies"
    show_root.mkdir()
    movie_root.mkdir()
    return SimpleNamespace(
        misc=SimpleNamespace(
            show_directory=show_root,
            movie_directory=movie_root,
            show_libraries=[],
            movie_libraries=[],
        )
    )


@pytest.fixture
def media_roots(tmp_path: Path) -> tuple[Path, Path]:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    return allowed, outside


def test_resolve_video_file_cached_hit_inside_root_validates_without_upsert(
    media_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, _outside = media_roots
    cached = allowed / "video.mkv"
    cached.touch()
    file_id = uuid.uuid4()
    validated: list[Path] = []
    remember = AsyncMock()

    def _spy_validate(path: Path, roots: list[Path]) -> None:
        validated.append(path)
        _validate_media_path(path, roots)

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        AsyncMock(return_value=cached),
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._validate_media_path",
        _spy_validate,
    )

    result = _run(
        _resolve_video_file(
            MagicMock(),
            file_id=file_id,
            media_type="episode",
            directory=allowed,
            stems=["video"],
            allowed_roots=[allowed],
        )
    )

    assert result == cached
    assert validated == [cached]
    remember.assert_not_awaited()


def test_resolve_video_file_cached_hit_outside_root_raises_404(
    media_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, outside = media_roots
    cached = outside / "escaped.mkv"
    cached.touch()
    remember = AsyncMock()

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        AsyncMock(return_value=cached),
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _resolve_video_file(
                MagicMock(),
                file_id=uuid.uuid4(),
                media_type="episode",
                directory=allowed,
                stems=["video"],
                allowed_roots=[allowed],
            )
        )

    assert exc_info.value.status_code == 404
    remember.assert_not_awaited()


def test_resolve_video_file_miss_validates_and_upserts_once(
    media_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, _outside = media_roots
    video = allowed / "episode.mkv"
    video.touch()
    file_id = uuid.uuid4()
    remember = AsyncMock()
    validated: list[Path] = []

    def _spy_validate(path: Path, roots: list[Path]) -> None:
        validated.append(path)
        _validate_media_path(path, roots)

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._find_first_video_file",
        lambda _directory, _stems: video,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._validate_media_path",
        _spy_validate,
    )

    result = _run(
        _resolve_video_file(
            MagicMock(),
            file_id=file_id,
            media_type="episode",
            directory=allowed,
            stems=["episode"],
            allowed_roots=[allowed],
        )
    )

    assert result == video
    assert validated == [video]
    remember.assert_awaited_once()


def test_resolve_video_file_miss_without_file_raises_404(
    media_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, _outside = media_roots
    remember = AsyncMock()

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._find_first_video_file",
        lambda _directory, _stems: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _resolve_video_file(
                MagicMock(),
                file_id=uuid.uuid4(),
                media_type="episode",
                directory=allowed,
                stems=["missing"],
                allowed_roots=[allowed],
            )
        )

    assert exc_info.value.status_code == 404
    remember.assert_not_awaited()


def test_resolve_episode_video_file_builds_allowed_roots_and_delegates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config_stub(tmp_path)
    ep_file = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=EpisodeId(uuid.uuid4()),
        quality=Quality.fullhd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
    )
    show_service = MagicMock()
    show_service.get_episode = AsyncMock(return_value=MagicMock(number=1))
    show_service.get_season_by_episode = AsyncMock(
        return_value=MagicMock(number=1, id=uuid.uuid4())
    )
    show_service.show_repository.get_show_by_season_id = AsyncMock(
        return_value=MagicMock()
    )
    show_service.get_root_season_directory.return_value = config.misc.show_directory
    delegated = AsyncMock(return_value=config.misc.show_directory / "video.mkv")
    monkeypatch.setattr("miramedia.streams.router.MiraMediaConfig", lambda: config)
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_video_file",
        delegated,
    )
    monkeypatch.setattr(
        "miramedia.streams.router.episode_file_stem_candidates",
        lambda *_args, **_kwargs: ["episode"],
    )

    result = _run(
        _resolve_episode_video_file(
            show_service=show_service,
            episode_file=ep_file,
            db=MagicMock(),
        )
    )

    assert result == config.misc.show_directory / "video.mkv"
    delegated.assert_awaited_once()
    kwargs = delegated.await_args.kwargs
    assert kwargs["media_type"] == "episode"
    assert kwargs["allowed_roots"] == [config.misc.show_directory]


def test_resolve_episode_video_file_not_found_raises_404() -> None:
    show_service = MagicMock()
    show_service.get_episode = AsyncMock(side_effect=NotFoundError("missing"))
    ep_file = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=EpisodeId(uuid.uuid4()),
        quality=Quality.fullhd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _resolve_episode_video_file(
                show_service=show_service,
                episode_file=ep_file,
                db=MagicMock(),
            )
        )

    assert exc_info.value.status_code == 404


def test_resolve_movie_video_file_builds_allowed_roots_and_delegates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config_stub(tmp_path)
    movie_repo = FakeMovieRepository()
    movie = make_movie(name="Resolve Movie")
    movie_repo.add_movie(movie)
    mov_file = MovieFile(
        id=uuid.uuid4(),
        movie_id=movie.id,
        quality=Quality.fullhd,
        import_status=ImportOutcome.imported,
    )
    from miramedia.movies.service import MovieService

    movie_service = MovieService(
        movie_repository=movie_repo,  # type: ignore[arg-type]
        torrent_service=MagicMock(),
        indexer_service=MagicMock(),
        notification_service=MagicMock(),
    )
    delegated = AsyncMock(return_value=config.misc.movie_directory / "video.mkv")
    monkeypatch.setattr("miramedia.streams.router.MiraMediaConfig", lambda: config)
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_video_file",
        delegated,
    )

    result = _run(
        _resolve_movie_video_file(
            movie=movie,
            movie_service=movie_service,
            movie_file=mov_file,
            db=MagicMock(),
        )
    )

    assert result == config.misc.movie_directory / "video.mkv"
    delegated.assert_awaited_once()
    kwargs = delegated.await_args.kwargs
    assert kwargs["media_type"] == "movie"
    assert kwargs["allowed_roots"] == [config.misc.movie_directory]


def test_resolve_subtitle_file_cached_hit_outside_root_raises_404(
    media_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, outside = media_roots
    cached = outside / "escaped.vtt"
    cached.write_text("WEBVTT\n", encoding="utf-8")
    remember = AsyncMock()

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        AsyncMock(return_value=cached),
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _resolve_subtitle_file(
                MagicMock(),
                file_id=uuid.uuid4(),
                media_type="movie",
                language="en",
                directory=allowed,
                stems=["show"],
                allowed_roots=[allowed],
            )
        )

    assert exc_info.value.status_code == 404
    remember.assert_not_awaited()


def test_resolve_subtitle_file_miss_upserts_once(
    media_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, _outside = media_roots
    sub = allowed / "show.en.vtt"
    sub.write_text("WEBVTT\n", encoding="utf-8")
    remember = AsyncMock()

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._find_first_subtitle_file",
        lambda _directory, _stems, _language: sub,
    )

    result = _run(
        _resolve_subtitle_file(
            MagicMock(),
            file_id=uuid.uuid4(),
            media_type="episode",
            language="en",
            directory=allowed,
            stems=["show"],
            allowed_roots=[allowed],
        )
    )

    assert result == sub
    remember.assert_awaited_once()

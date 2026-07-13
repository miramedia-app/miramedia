"""Path resolution characterization for integrity batching."""

from __future__ import annotations

from pathlib import Path

from miramedia.torrents.integrity import (
    resolve_episode_file_path_in_memory,
    resolve_movie_file_path_in_memory,
)
from miramedia.torrents.schemas import Quality
from tests.fakes.repositories import make_movie, make_show


def test_resolve_episode_path_finds_video(tmp_path: Path) -> None:
    show = make_show(name="Severance", season_number=1, episode_number=7)
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    video = season_dir / "Severance S01E07 - 1080p.mkv"
    video.write_bytes(b"x")

    class _Row:
        quality = Quality.fullhd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    path = resolve_episode_file_path_in_memory(
        show=show,
        season_number=1,
        episode_number=7,
        episode_file=_Row(),
        season_dir=season_dir,
    )
    assert path == video


def test_resolve_episode_path_missing_directory() -> None:
    show = make_show(name="Missing", season_number=1, episode_number=1)

    class _Row:
        quality = Quality.hd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    assert (
        resolve_episode_file_path_in_memory(
            show=show,
            season_number=1,
            episode_number=1,
            episode_file=_Row(),
            season_dir=Path("/no/such/season"),
        )
        is None
    )


def test_resolve_movie_path_finds_video(tmp_path: Path) -> None:
    movie = make_movie(name="Dune")
    movie_root = tmp_path / "Dune (2020)"
    movie_root.mkdir()
    video = movie_root / "Dune (2020) - 2160p.mkv"
    video.write_bytes(b"x")

    class _Row:
        quality = Quality.uhd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    path = resolve_movie_file_path_in_memory(
        movie=movie,
        movie_file=_Row(),
        movie_root=movie_root,
    )
    assert path == video


def test_resolve_movie_path_missing_file(tmp_path: Path) -> None:
    movie = make_movie(name="Empty")
    movie_root = tmp_path / "Empty"
    movie_root.mkdir()

    class _Row:
        quality = Quality.hd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    assert (
        resolve_movie_file_path_in_memory(
            movie=movie,
            movie_file=_Row(),
            movie_root=movie_root,
        )
        is None
    )

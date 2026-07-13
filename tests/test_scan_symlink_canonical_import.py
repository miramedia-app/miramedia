"""Symlinked library roots must not trigger dot-rename of canonical dirs."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from miramedia.config import LibraryItem
from miramedia.media_paths import paths_same_canonical
from miramedia.naming import movie_folder_name, show_folder_name
from miramedia.naming_defaults import (
    DEFAULT_EPISODE_FILE_FORMAT,
    DEFAULT_MOVIE_FILE_FORMAT,
    DEFAULT_MOVIE_FOLDER_FORMAT,
    DEFAULT_SEASON_FOLDER_FORMAT,
    DEFAULT_SHOW_FOLDER_FORMAT,
)
from tests.fakes.repositories import make_movie, make_show
from tests.fakes.services import build_movie_service, build_show_service, run_async


def _patch_library_config(
    monkeypatch: pytest.MonkeyPatch, misc: types.SimpleNamespace
) -> None:
    def _cfg() -> types.SimpleNamespace:
        return types.SimpleNamespace(misc=misc)

    for target in (
        "miramedia.media_paths.MiraMediaConfig",
        "miramedia.naming.MiraMediaConfig",
        "miramedia.shows.service.MiraMediaConfig",
        "miramedia.movies.service.MiraMediaConfig",
    ):
        monkeypatch.setattr(target, _cfg)


@pytest.fixture
def symlinked_default_show_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    real_root = tmp_path / "real-shows"
    real_root.mkdir()
    link_root = tmp_path / "shows-link"
    link_root.symlink_to(real_root)
    misc = types.SimpleNamespace(
        show_directory=link_root,
        movie_directory=tmp_path / "movies",
        show_libraries=(),
        movie_libraries=(),
        naming=types.SimpleNamespace(
            show_folder_format=DEFAULT_SHOW_FOLDER_FORMAT,
            movie_folder_format=DEFAULT_MOVIE_FOLDER_FORMAT,
            season_folder_format=DEFAULT_SEASON_FOLDER_FORMAT,
            movie_file_format=DEFAULT_MOVIE_FILE_FORMAT,
            episode_file_format=DEFAULT_EPISODE_FILE_FORMAT,
        ),
    )
    _patch_library_config(monkeypatch, misc)
    return link_root, real_root


@pytest.fixture
def symlinked_named_movie_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    real_root = tmp_path / "real-4k"
    real_root.mkdir()
    link_root = tmp_path / "movies-4k-link"
    link_root.symlink_to(real_root)
    misc = types.SimpleNamespace(
        show_directory=tmp_path / "shows",
        movie_directory=tmp_path / "default-movies",
        show_libraries=(),
        movie_libraries=(LibraryItem(name="4K", path=str(link_root)),),
        naming=types.SimpleNamespace(
            show_folder_format=DEFAULT_SHOW_FOLDER_FORMAT,
            movie_folder_format=DEFAULT_MOVIE_FOLDER_FORMAT,
            season_folder_format=DEFAULT_SEASON_FOLDER_FORMAT,
            movie_file_format=DEFAULT_MOVIE_FILE_FORMAT,
            episode_file_format=DEFAULT_EPISODE_FILE_FORMAT,
        ),
    )
    _patch_library_config(monkeypatch, misc)
    return link_root, real_root


def test_paths_same_canonical_across_symlinked_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    left = (real / "Title").resolve()
    right = link / "Title"
    assert paths_same_canonical(left, right)


def test_import_show_from_directory_does_not_rename_canonical_symlink_target(
    symlinked_default_show_root: tuple[Path, Path],
) -> None:
    link_root, real_root = symlinked_default_show_root
    show = make_show(name="Canonical Show", year=2020)
    folder_name = show_folder_name(show)
    canonical_lexical = link_root / folder_name
    canonical_lexical.mkdir(parents=True)
    source_resolved = (real_root / folder_name).resolve()
    assert paths_same_canonical(source_resolved, canonical_lexical)

    service, show_repo, _ = build_show_service()
    show_repo.add_show(show)

    run_async(service.import_show_from_directory(show, source_resolved))

    assert canonical_lexical.exists()
    assert source_resolved.exists()
    assert not canonical_lexical.name.startswith(".")
    assert not (canonical_lexical.parent / f".{folder_name}").exists()


def test_import_movie_from_directory_does_not_rename_canonical_symlink_target(
    symlinked_named_movie_root: tuple[Path, Path],
) -> None:
    link_root, real_root = symlinked_named_movie_root
    movie = make_movie(name="Canonical Movie", year=2021)
    movie.library = "4K"
    folder_name = movie_folder_name(movie)
    canonical_lexical = link_root / folder_name
    canonical_lexical.mkdir(parents=True)
    source_resolved = (real_root / folder_name).resolve()

    service, movie_repo, _ = build_movie_service()
    movie_repo.add_movie(movie)

    run_async(service.import_movie_from_directory(movie, source_resolved))

    assert canonical_lexical.exists()
    assert source_resolved.exists()
    assert not canonical_lexical.name.startswith(".")
    assert not (canonical_lexical.parent / f".{folder_name}").exists()

"""Characterization tests for move_media_library and refresh_metadata_with_fallback."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.config import NamingConfig
from miramedia.exceptions import BadRequestError
from miramedia.imports.files import import_file as real_import_file
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show
from tests.fakes import build_movie_service, build_show_service, run_async
from tests.fakes.repositories import FakeShowRepository, make_movie, make_show

MediaKind = Literal["show", "movie"]


def _config_stub(tmp_path: Path, kind: MediaKind) -> SimpleNamespace:
    show_root = tmp_path / "shows"
    movie_root = tmp_path / "movies"
    secondary_root = tmp_path / "secondary"
    show_root.mkdir()
    movie_root.mkdir()
    secondary_root.mkdir()
    misc: dict[str, object] = {
        "show_directory": show_root,
        "movie_directory": movie_root,
        "naming": NamingConfig(),
    }
    if kind == "show":
        misc["show_libraries"] = [
            SimpleNamespace(name="Secondary", path=str(secondary_root)),
        ]
        misc["movie_libraries"] = []
    else:
        misc["show_libraries"] = []
        misc["movie_libraries"] = [
            SimpleNamespace(name="Secondary", path=str(secondary_root)),
        ]
    return SimpleNamespace(misc=SimpleNamespace(**misc))


def _patch_config(
    monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace, kind: MediaKind
) -> None:
    if kind == "show":
        monkeypatch.setattr("miramedia.shows.service.MiraMediaConfig", lambda: config)
    else:
        monkeypatch.setattr("miramedia.movies.service.MiraMediaConfig", lambda: config)
    monkeypatch.setattr("miramedia.naming.MiraMediaConfig", lambda: config)


def _file_tree(root: Path) -> dict[str, int]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.stat().st_size
        for path in root.rglob("*")
        if path.is_file()
    }


def _build_media_tree(svc, media: Show | Movie, files: dict[str, str]) -> Path:
    root = svc.get_root_media_directory(media)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _secondary_library_path(config: SimpleNamespace) -> Path:
    if config.misc.show_libraries:
        return Path(config.misc.show_libraries[0].path)
    return Path(config.misc.movie_libraries[0].path)


@pytest.fixture(params=["show", "movie"])
def move_env(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    kind: MediaKind = request.param
    config = _config_stub(tmp_path, kind)
    _patch_config(monkeypatch, config, kind)
    if kind == "show":
        media = make_show(name="Move Test", year=2020)
        show_repo = FakeShowRepository()
        show_repo.add_show(media)
        svc, _repo, _torrent_repo = build_show_service(show_repo=show_repo)
        show_repo.db = MagicMock()
        move_fn = svc.move_show_library
        unknown_library = "Unknown show library"
    else:
        media = make_movie(name="Move Test", year=2020)
        movie_repo = build_movie_service()[1]
        movie_repo.add_movie(media)
        svc, movie_repo, _torrent_repo = build_movie_service(movie_repo=movie_repo)
        movie_repo.db = MagicMock()
        move_fn = svc.move_movie_library
        unknown_library = "Unknown movie library"

    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io",
        AsyncMock(),
    )
    return kind, svc, media, config, move_fn, unknown_library


def test_move_media_library_happy_path_removes_source_and_updates_library(
    move_env,
) -> None:
    _kind, svc, media, config, move_fn, _unknown = move_env
    files = {
        "Season 01/episode1.mkv": "ep1",
        "Season 02/episode2.mkv": "ep2",
    }
    source_root = _build_media_tree(svc, media, files)
    source_snapshot = _file_tree(source_root)

    set_library = AsyncMock()
    with patch.object(svc, "_set_media_library", set_library):
        result = run_async(move_fn(media, "Secondary"))

    dest_path = _secondary_library_path(config) / source_root.name
    assert result["library_changed"] is True
    assert result["moved"] == 2
    assert not result.get("errors")
    assert not source_root.exists()
    assert _file_tree(dest_path) == source_snapshot
    set_library.assert_awaited_once_with(media.id, "Secondary")


def test_move_media_library_link_failure_keeps_source_intact(
    move_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kind, svc, media, config, move_fn, _unknown = move_env
    files = {
        "Season 01/episode1.mkv": "ep1",
        "Season 02/episode2.mkv": "ep2",
    }
    source_root = _build_media_tree(svc, media, files)
    source_snapshot = _file_tree(source_root)
    calls = 0

    def flaky_import(target_file: Path, source_file: Path, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            msg = "injected link failure"
            raise OSError(msg)
        real_import_file(target_file, source_file, **kwargs)

    monkeypatch.setattr("miramedia.media_service.import_file", flaky_import)
    set_library = AsyncMock()
    with patch.object(svc, "_set_media_library", set_library):
        result = run_async(move_fn(media, "Secondary"))

    dest_path = _secondary_library_path(config) / source_root.name
    # Pins today's behavior: partial copy is left stranded under the destination
    # (no rollback) pending a product decision on rollback vs resumable retry.
    dest_snapshot = {
        "Season 01/episode1.mkv": source_snapshot["Season 01/episode1.mkv"],
    }

    assert result["library_changed"] is False
    assert result["errors"]
    assert _file_tree(source_root) == source_snapshot
    assert _file_tree(dest_path) == dest_snapshot
    set_library.assert_not_awaited()


def test_move_media_library_delete_source_false_retains_source(
    move_env,
) -> None:
    _kind, svc, media, config, move_fn, _unknown = move_env
    files = {"Season 01/episode1.mkv": "ep1"}
    source_root = _build_media_tree(svc, media, files)
    source_snapshot = _file_tree(source_root)

    with patch.object(svc, "_set_media_library", AsyncMock()):
        result = run_async(move_fn(media, "Secondary", delete_source=False))

    dest_root = _secondary_library_path(config) / source_root.name
    assert result["library_changed"] is True
    assert _file_tree(source_root) == source_snapshot
    assert _file_tree(dest_root) == source_snapshot


def test_move_media_library_missing_source_short_circuits(move_env) -> None:
    _kind, svc, media, _config, move_fn, _unknown = move_env
    source_root = svc.get_root_media_directory(media)
    assert not source_root.exists()

    set_library = AsyncMock()
    with patch.object(svc, "_set_media_library", set_library):
        result = run_async(move_fn(media, "Secondary"))

    assert result == {
        "moved": 0,
        "skipped": True,
        "reason": "source directory missing",
    }
    set_library.assert_awaited_once_with(media.id, "Secondary")


def test_move_media_library_unknown_library_raises_value_error(move_env) -> None:
    _kind, _svc, media, _config, move_fn, unknown_library = move_env
    with pytest.raises(ValueError, match=unknown_library):
        run_async(move_fn(media, "NoSuchLibrary"))


@pytest.fixture
def show_move_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = _config_stub(tmp_path, "show")
    _patch_config(monkeypatch, config, "show")
    show = make_show(name="Move Test", year=2020)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    svc, show_repo, _ = build_show_service(show_repo=show_repo)
    show_repo.db = MagicMock()
    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io",
        AsyncMock(),
    )
    return svc, show, config


def test_refresh_metadata_with_fallback_uses_configured_provider(show_move_env) -> None:
    svc, show, _config = show_move_env
    provider = MagicMock()
    refresh = AsyncMock()

    with (
        patch(
            "miramedia.media_service._metadata_provider_for",
            return_value=provider,
        ),
        patch.object(svc, "_refresh_update_metadata", refresh),
    ):
        run_async(svc.refresh_metadata_with_fallback(show))

    refresh.assert_awaited_once_with(show, provider)


def test_refresh_metadata_with_fallback_raises_when_no_provider_matches(
    show_move_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, show, _config = show_move_env
    show = show.model_copy(
        update={"metadata_provider": "invalid", "imdb_id": None, "name": "Missing"}
    )

    monkeypatch.setattr(
        "miramedia.media_service._metadata_provider_for",
        MagicMock(side_effect=RuntimeError("disabled")),
    )
    monkeypatch.setattr(
        "miramedia.metadata.dependencies.get_all_enabled_providers",
        list,
    )

    with pytest.raises(BadRequestError, match="Cannot refresh metadata"):
        run_async(svc.refresh_metadata_with_fallback(show))

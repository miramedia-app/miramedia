"""Library-root probe: bounded stat, never a media walk."""

from __future__ import annotations

import ast
from pathlib import Path

from miramedia.config import BasicConfig, LibraryItem
from miramedia.storage.service import configured_library_roots, probe_library_root
from miramedia.storage.volumes import probe_storage_volumes, probe_volume

_STORAGE_ROOT = Path(__file__).resolve().parents[1] / "miramedia" / "storage"


def _diagnostics_imports_in_storage() -> list[str]:
    offenders: list[str] = []
    for path in sorted(_STORAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_STORAGE_ROOT.parent.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{rel}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("miramedia.diagnostics")
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("miramedia.diagnostics"):
                    offenders.append(f"{rel}: from {node.module} import ...")
    return offenders


def test_storage_package_does_not_import_diagnostics() -> None:
    assert _diagnostics_imports_in_storage() == []


def test_probe_ok_directory(tmp_path: Path) -> None:
    root = tmp_path / "shows"
    root.mkdir()
    probe = probe_library_root("Default", "show", root)
    assert probe.ok is True
    assert probe.error is None


def test_probe_missing_path(tmp_path: Path) -> None:
    probe = probe_library_root("Default", "show", tmp_path / "missing")
    assert probe.ok is False
    assert probe.error == "missing"


def test_probe_not_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "file"
    file_path.write_text("x")
    probe = probe_library_root("NAS", "movie", file_path)
    assert probe.ok is False
    assert probe.error == "not_a_directory"


def test_probe_unset_name() -> None:
    probe = probe_library_root("Empty", "show", "  ")
    assert probe.ok is False
    assert probe.error == "unset_name"


def test_configured_roots_are_defaults_plus_named(tmp_path: Path) -> None:
    shows = tmp_path / "shows"
    movies = tmp_path / "movies"
    nas = tmp_path / "nas"
    shows.mkdir()
    movies.mkdir()
    nas.mkdir()
    misc = BasicConfig(
        show_directory=shows,
        movie_directory=movies,
        show_libraries=[LibraryItem(name="NAS", path=str(nas))],
        movie_libraries=[],
    )
    probes = configured_library_roots(misc)
    names = {(p.name, p.kind) for p in probes}
    assert names == {("Default", "show"), ("Default", "movie"), ("NAS", "show")}
    assert all(p.ok for p in probes)
    assert not any("iterdir" in (p.error or "") for p in probes)


def test_probe_volume_reports_bytes_for_existing_root(tmp_path: Path) -> None:
    root = tmp_path / "shows"
    root.mkdir()
    volume = probe_volume("Shows (Default)", root)
    assert volume.error is None
    assert volume.total_bytes is not None
    assert volume.total_bytes > 0
    assert volume.free_bytes is not None
    assert volume.used_bytes is not None
    assert volume.path == str(root)


def test_probe_volume_unset_path() -> None:
    volume = probe_volume("Empty", "  ")
    assert volume.error == "unset"
    assert volume.total_bytes is None


def test_probe_storage_volumes_includes_named_library(tmp_path: Path) -> None:
    shows = tmp_path / "shows"
    movies = tmp_path / "movies"
    nas = tmp_path / "nas"
    images = tmp_path / "images"
    torrents = tmp_path / "torrents"
    for path in (shows, movies, nas, images, torrents):
        path.mkdir()
    misc = BasicConfig(
        show_directory=shows,
        movie_directory=movies,
        image_directory=images,
        torrent_directory=torrents,
        incomplete_torrent_path="",
        show_libraries=[LibraryItem(name="NAS", path=str(nas))],
        movie_libraries=[],
    )
    labels = {volume.label for volume in probe_storage_volumes(misc)}
    assert "Shows (Default)" in labels
    assert "Movies (Default)" in labels
    assert "Shows (NAS)" in labels
    assert "Images" in labels
    assert "Downloads" in labels
    assert "Incomplete downloads" not in labels

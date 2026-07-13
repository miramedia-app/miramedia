"""Parent-path containment tests for descriptor-bound archive staging."""

from __future__ import annotations

import os
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    extract_archive_to_directory,
)
from miramedia.imports.archive_staging_io import STAGING_DIR_PREFIX
from tests.archive_test_helpers import container_paths, payload_file


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _swap_parent_path(parent: Path, outside: Path) -> None:
    hidden = parent.with_name(f"{parent.name}-hidden")
    parent.rename(hidden)
    outside.mkdir(exist_ok=True)
    parent.symlink_to(outside)


def _hidden_parent(parent: Path) -> Path:
    return parent.with_name(f"{parent.name}-hidden")


def test_parent_swap_after_bind_before_staging_write_stays_contained(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    dest = parent / "import"
    outside = tmp_path / "outside"
    parent.mkdir()
    dest.mkdir()
    outside.mkdir()
    keeper = outside / "keeper.txt"
    keeper.write_bytes(b"safe")
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"clip.mkv": b"video"})

    from miramedia.imports import archive_extraction as extraction
    from miramedia.imports.archive_publication import bind_directory

    parent_fd = bind_directory(parent)
    _swap_parent_path(parent, outside)
    staging = extraction._create_staging_dir(parent_fd)
    try:
        extraction._extract_to_staging(archive, staging, "zip")
    finally:
        staging.close()
        os.close(parent_fd)

    assert keeper.read_bytes() == b"safe"
    assert not (outside / "clip.mkv").exists()
    hidden_entries = list(_hidden_parent(parent).glob(f"{STAGING_DIR_PREFIX}*"))
    assert hidden_entries
    assert (hidden_entries[0] / "clip.mkv").read_bytes() == b"video"


def test_parent_swap_after_staging_before_first_write_stays_contained(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    dest = parent / "import"
    outside = tmp_path / "outside"
    parent.mkdir()
    dest.mkdir()
    outside.mkdir()
    keeper = outside / "keeper.txt"
    keeper.write_bytes(b"safe")
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"clip.mkv": b"payload"})

    from miramedia.imports import archive_extraction as extraction
    from miramedia.imports.archive_publication import bind_directory

    parent_fd = bind_directory(parent)
    staging = extraction._create_staging_dir(parent_fd)
    try:
        _swap_parent_path(parent, outside)
        extraction._extract_to_staging(archive, staging, "zip")
    finally:
        staging.close()
        os.close(parent_fd)

    assert keeper.read_bytes() == b"safe"
    assert not (outside / "clip.mkv").exists()
    hidden_entries = list(_hidden_parent(parent).glob(f"{STAGING_DIR_PREFIX}*"))
    assert hidden_entries
    assert (hidden_entries[0] / "clip.mkv").read_bytes() == b"payload"


def test_parent_swap_during_first_write_via_full_pipeline_leaves_outside_clean(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    dest = parent / "import"
    outside = tmp_path / "outside"
    parent.mkdir()
    dest.mkdir()
    outside.mkdir()
    keeper = outside / "keeper.txt"
    keeper.write_bytes(b"safe")
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"clip.mkv": b"payload"})

    from miramedia.imports import archive_staging_io as staging_io

    real_open = staging_io.open_entry_for_write

    @contextmanager
    def _swap_wrapper(staging_fd: int, entry_name: str):
        if entry_name == "clip.mkv":
            _swap_parent_path(parent, outside)
        with real_open(staging_fd, entry_name) as fd:
            yield fd

    with patch.object(staging_io, "open_entry_for_write", side_effect=_swap_wrapper):
        extract_archive_to_directory(archive, dest)

    assert keeper.read_bytes() == b"safe"
    assert not (outside / "clip.mkv").exists()
    hidden_dest = parent.with_name("parent-hidden") / "import"
    assert payload_file(hidden_dest, "clip.mkv").read_bytes() == b"payload"


def test_failed_extract_cleans_only_owned_staging_after_parent_swap(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    dest = parent / "import"
    outside = tmp_path / "outside"
    parent.mkdir()
    dest.mkdir()
    archive = tmp_path / "bad.zip"
    _write_zip(archive, {"../escape.mkv": b"x"})

    from miramedia.imports import archive_extraction as extraction
    from miramedia.imports.archive_publication import bind_directory

    parent_fd = bind_directory(parent)
    staging = extraction._create_staging_dir(parent_fd)
    _swap_parent_path(parent, outside)
    replacement = outside / f"{STAGING_DIR_PREFIX}replacement"
    replacement.mkdir()
    (replacement / "marker").write_bytes(b"safe")

    with pytest.raises(ArchiveExtractionError):
        extraction._extract_to_staging(archive, staging, "zip")
    extraction._cleanup_staging(staging)

    assert (replacement / "marker").read_bytes() == b"safe"
    assert container_paths(dest) == []

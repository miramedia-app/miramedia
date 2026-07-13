"""Import discovery policy for published archive containers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from miramedia.imports.archive_extraction import extract_archive_to_directory
from miramedia.imports.archive_publication import (
    ARCHIVE_CONTAINER_PREFIX,
    PRIVATE_BUILD_PREFIX,
    QUARANTINE_PREFIX,
    completion_marker_name,
)
from miramedia.imports.archive_staging_io import STAGING_DIR_PREFIX
from miramedia.imports.files import get_files_for_import
from tests.archive_test_helpers import container_paths, payload_file


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_get_files_for_import_discovers_only_verified_payload(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"movie.mkv": b"video", "readme.txt": b"notes"})
    extract_archive_to_directory(archive, tmp_path)

    video_files, _subtitle_files, all_files = get_files_for_import(tmp_path)

    published_video = payload_file(tmp_path, "movie.mkv")
    assert published_video in all_files
    assert video_files == [published_video]
    assert all("payload" in path.parts for path in all_files)


def test_get_files_for_import_ignores_internal_debris_and_root_attacker(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"movie.mkv": b"video"})
    extract_archive_to_directory(archive, tmp_path)

    (tmp_path / "attacker.mkv").write_bytes(b"evil")
    (tmp_path / f"{STAGING_DIR_PREFIX}leftover").mkdir()
    (tmp_path / f"{PRIVATE_BUILD_PREFIX}leftover").mkdir()
    (tmp_path / f"{QUARANTINE_PREFIX}leftover").mkdir()
    malformed = tmp_path / f"{ARCHIVE_CONTAINER_PREFIX}deadbeef"
    malformed.mkdir()
    (malformed / "attacker.mkv").write_bytes(b"evil")

    _video_files, _subtitle_files, all_files = get_files_for_import(tmp_path)

    assert payload_file(tmp_path, "movie.mkv") in all_files
    assert tmp_path / "attacker.mkv" not in all_files
    assert not any("leftover" in str(path) for path in all_files)
    assert not any(malformed.name in str(path) for path in all_files)


def test_get_files_for_import_ignores_incomplete_container_without_marker(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"movie.mkv": b"video"})
    extract_archive_to_directory(archive, tmp_path)

    container = container_paths(tmp_path)[0]
    marker = container / completion_marker_name(
        container.name.removeprefix(ARCHIVE_CONTAINER_PREFIX)
    )
    marker.unlink()
    archive.unlink()

    _video_files, _subtitle_files, all_files = get_files_for_import(tmp_path)

    assert all_files == []


def test_get_files_for_import_preserves_ordinary_flat_media(tmp_path: Path) -> None:
    existing = tmp_path / "keeper.mkv"
    existing.write_bytes(b"keeper")

    _video_files, _subtitle_files, all_files = get_files_for_import(tmp_path)

    assert existing in all_files
    assert _video_files == [existing]

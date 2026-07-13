"""Security regression tests for safe archive extraction."""

from __future__ import annotations

import bz2
import gzip
import io
import stat
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    MAX_ARCHIVE_ENTRIES,
    MAX_EXPANDED_BYTES,
    ArchiveExtractionError,
    extract_archive_to_directory,
)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _write_tar(path: Path, entries: dict[str, bytes], *, gzip_compressed: bool = False) -> None:
    mode = "w:gz" if gzip_compressed else "w"
    with tarfile.open(path, mode) as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


# ---------------------------------------------------------------------------
# Valid archives
# ---------------------------------------------------------------------------


def test_extract_valid_zip(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"movie/movie.mkv": b"video-bytes", "subs/en.srt": b"subs"})

    extract_archive_to_directory(archive, dest)

    assert (dest / "movie" / "movie.mkv").read_bytes() == b"video-bytes"
    assert (dest / "subs" / "en.srt").read_bytes() == b"subs"


def test_extract_valid_tar(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_tar(archive, {"clip.mkv": b"tar-video"})

    extract_archive_to_directory(archive, dest)

    assert (dest / "clip.mkv").read_bytes() == b"tar-video"


def test_extract_valid_tar_gz(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_tar(archive, {"clip.mkv": b"gz-video"}, gzip_compressed=True)

    extract_archive_to_directory(archive, dest)

    assert (dest / "clip.mkv").read_bytes() == b"gz-video"


def test_extract_valid_gzip_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(b"gzip-video"))

    extract_archive_to_directory(archive, dest)

    assert (dest / "clip.mkv").read_bytes() == b"gzip-video"


def test_extract_valid_bzip2_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.bz2"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(bz2.compress(b"bz2-video"))

    extract_archive_to_directory(archive, dest)

    assert (dest / "clip.mkv").read_bytes() == b"bz2-video"


# ---------------------------------------------------------------------------
# Traversal / unsafe names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry_name",
    [
        "../escape.mkv",
        "/absolute.mkv",
        "foo/../../outside.mkv",
        "foo\\bar.mkv",
    ],
)
def test_zip_rejects_unsafe_entry_names(
    tmp_path: Path,
    entry_name: str,
) -> None:
    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {entry_name: b"x"})

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_zip_rejects_symlink_metadata(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link.mkv")
        info.external_attr = (stat.S_IFLNK | 0o755) << 16
        zf.writestr(info, b"target")

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_tar_rejects_symlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("link.mkv")
        info.type = tarfile.SYMTYPE
        info.linkname = "real.mkv"
        tf.addfile(info)

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------


def test_zip_rejects_too_many_entries(tmp_path: Path) -> None:
    archive = tmp_path / "many.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    with patch(
        "miramedia.imports.archive_extraction.MAX_ARCHIVE_ENTRIES",
        3,
    ):
        _write_zip(archive, {f"f{i}.txt": b"x" for i in range(4)})

        with pytest.raises(ArchiveExtractionError):
            extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_zip_rejects_expanded_size_limit(tmp_path: Path) -> None:
    archive = tmp_path / "big.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        10,
    ):
        _write_zip(archive, {"a.bin": b"x" * 8, "b.bin": b"y" * 8})

        with pytest.raises(ArchiveExtractionError):
            extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


# ---------------------------------------------------------------------------
# Failure cleanup and collision policy
# ---------------------------------------------------------------------------


def test_failure_leaves_preexisting_destination_files(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    keeper = dest / "keeper.mkv"
    keeper.write_bytes(b"keep-me")
    _write_zip(archive, {"../escape.mkv": b"x"})

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert keeper.read_bytes() == b"keep-me"
    assert not any(dest.glob("escape.mkv"))


def test_promotion_rejects_name_collision(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    existing = dest / "clip.mkv"
    existing.write_bytes(b"original")
    _write_zip(archive, {"clip.mkv": b"new-bytes"})

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert existing.read_bytes() == b"original"


def test_staging_cleaned_up_after_success(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"ok"})

    from miramedia.imports import archive_extraction as mod

    created: list[Path] = []
    real_create = mod._create_staging_dir

    def _track(parent: Path) -> Path:
        staging = real_create(parent)
        created.append(staging)
        return staging

    with patch.object(mod, "_create_staging_dir", side_effect=_track):
        extract_archive_to_directory(archive, dest)

    assert created
    assert not created[0].exists()


def test_staging_cleaned_up_after_failure(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"../escape.mkv": b"x"})

    from miramedia.imports import archive_extraction as mod

    created: list[Path] = []
    real_create = mod._create_staging_dir

    def _track(parent: Path) -> Path:
        staging = real_create(parent)
        created.append(staging)
        return staging

    with (
        patch.object(mod, "_create_staging_dir", side_effect=_track),
        pytest.raises(ArchiveExtractionError),
    ):
        extract_archive_to_directory(archive, dest)

    assert created
    assert not created[0].exists()


def test_extractor_timeout_reports_and_cleans_up(tmp_path: Path) -> None:
    archive = tmp_path / "slow.rar"
    archive.write_bytes(b"not a real rar")
    dest = tmp_path / "import"
    dest.mkdir()

    def _timeout(archive: Path, staging: Path) -> None:  # noqa: ARG001
        msg = "archive extraction timed out"
        raise ArchiveExtractionError(msg)

    with (
        patch(
            "miramedia.imports.archive_extraction._detect_format",
            return_value="rar",
        ),
        patch(
            "miramedia.imports.archive_extraction._extract_with_patool_subprocess",
            side_effect=_timeout,
        ),
        pytest.raises(ArchiveExtractionError, match="timed out"),
    ):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


# ---------------------------------------------------------------------------
# Policy constants are documented by tests
# ---------------------------------------------------------------------------


def test_staging_directory_permissions(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})

    from miramedia.imports import archive_extraction as mod

    observed: list[int] = []
    original = mod._create_staging_dir

    def _capture_mode(parent: Path) -> Path:
        staging = original(parent)
        observed.append(stat.S_IMODE(staging.stat().st_mode))
        return staging

    with patch.object(mod, "_create_staging_dir", side_effect=_capture_mode):
        extract_archive_to_directory(archive, dest)

    assert observed == [0o700]


def test_get_files_for_import_discovers_extracted_video(tmp_path: Path) -> None:
    from miramedia.imports.files import get_files_for_import

    archive = tmp_path / "release.zip"
    _write_zip(archive, {"movie.mkv": b"video-bytes", "readme.txt": b"notes"})

    video_files, subtitle_files, all_files = get_files_for_import(tmp_path)

    assert (tmp_path / "movie.mkv") in all_files
    assert video_files == [tmp_path / "movie.mkv"]
    assert subtitle_files == []


def test_policy_constants() -> None:
    assert MAX_ARCHIVE_ENTRIES == 10_000
    assert MAX_EXPANDED_BYTES == 50 * 1024**3

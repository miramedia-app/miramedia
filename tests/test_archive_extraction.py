"""Security regression tests for safe archive extraction."""

from __future__ import annotations

import bz2
import gzip
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    MAX_ARCHIVE_ENTRIES,
    MAX_EXPANDED_BYTES,
    RETAINED_ARCHIVE_FORMATS,
    UNSUPPORTED_ARCHIVE_FORMATS,
    ArchiveExtractionError,
    classify_archive,
    extract_archive_to_directory,
    is_archive_mime,
)
from tests.archive_test_helpers import container_paths, payload_file


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _write_tar(
    path: Path, entries: dict[str, bytes], *, gzip_compressed: bool = False
) -> None:
    mode = "w:gz" if gzip_compressed else "w"
    with tarfile.open(path, mode) as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _write_tar_bz2(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:bz2") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


# ---------------------------------------------------------------------------
# Format matrix
# ---------------------------------------------------------------------------


def test_format_matrix_documents_retained_and_unsupported() -> None:
    assert RETAINED_ARCHIVE_FORMATS == frozenset(
        {"zip", "tar", "tar.gz", "tar.bz2", "gzip", "bzip2"}
    )
    assert UNSUPPORTED_ARCHIVE_FORMATS == frozenset(
        {"rar", "7z", "freearc", "tar.xz", "zip64"},
    )
    assert is_archive_mime("application/vnd.rar")
    assert is_archive_mime("application/zip")


@pytest.mark.parametrize("filename", ["release.rar", "release.7z", "release.arc"])
def test_unsupported_formats_fail_closed(tmp_path: Path, filename: str) -> None:
    archive = tmp_path / filename
    archive.write_bytes(b"not-a-real-archive")
    dest = tmp_path / "import"
    dest.mkdir()

    with pytest.raises(
        ArchiveExtractionError, match="not supported for safe extraction"
    ):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


# ---------------------------------------------------------------------------
# Valid archives
# ---------------------------------------------------------------------------


def test_extract_valid_zip(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"movie/movie.mkv": b"video-bytes", "subs/en.srt": b"subs"})

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "movie", "movie.mkv").read_bytes() == b"video-bytes"
    assert payload_file(dest, "subs", "en.srt").read_bytes() == b"subs"


def test_extract_valid_tar(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_tar(archive, {"clip.mkv": b"tar-video"})

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"tar-video"


def test_extract_valid_tar_gz(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_tar(archive, {"clip.mkv": b"gz-video"}, gzip_compressed=True)

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"gz-video"


def test_extract_valid_tar_bz2(tmp_path: Path) -> None:
    archive = tmp_path / "release.tar.bz2"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_tar_bz2(archive, {"clip.mkv": b"bz2-tar-video"})

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"bz2-tar-video"


def test_extract_valid_gzip_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(b"gzip-video"))

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"gzip-video"


def test_extract_valid_bzip2_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.bz2"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(bz2.compress(b"bz2-video"))

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"bz2-video"


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
        "C:/windows.mkv",
        "C:relative.mkv",
        "//server/share/file.mkv",
        "\\\\server\\share\\file.mkv",
        "\\\\.\\PhysicalDrive0\\file.mkv",
        "encoded/%2e%2e/escape.mkv",
        "encoded/%2f/abs.mkv",
        "encoded/%5c/win.mkv",
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


def test_zip_keeps_double_encoded_percent_literal_and_confined(tmp_path: Path) -> None:
    archive = tmp_path / "encoded.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    entry = "literal/%252e%252e/stays.mkv"
    _write_zip(archive, {entry: b"literal-bytes"})

    extract_archive_to_directory(archive, dest)

    assert (
        payload_file(dest, "literal", "%252e%252e", "stays.mkv")
    ).read_bytes() == b"literal-bytes"


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


def test_tar_rejects_hardlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "hardlink.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("link.mkv")
        info.type = tarfile.LNKTYPE
        info.linkname = "real.mkv"
        tf.addfile(info)

    with pytest.raises(ArchiveExtractionError, match="unsupported tar entry type"):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_tar_rejects_device_member(tmp_path: Path) -> None:
    archive = tmp_path / "device.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("dev.zero")
        info.type = tarfile.CHRTYPE
        tf.addfile(info)

    with pytest.raises(ArchiveExtractionError, match="unsupported tar entry type"):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_staging_rejects_dangling_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"ok"})

    from miramedia.imports import archive_staging_io as staging_io

    real_collect = staging_io.collect_validated_regular_files

    def _inject_dangling(staging_fd: int) -> None:
        os.symlink("missing-target", "broken", dir_fd=staging_fd)
        real_collect(staging_fd)

    with (
        patch.object(
            staging_io,
            "collect_validated_regular_files",
            side_effect=_inject_dangling,
        ),
        pytest.raises(ArchiveExtractionError, match="symlink"),
    ):
        extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_classify_archive_recognizes_mkv_gz_by_extension(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.gz"
    archive.write_bytes(gzip.compress(b"x"))

    classification = classify_archive(archive)

    assert classification is not None
    assert classification.format == "gzip"
    assert classification.disposition == "retained"


def test_classify_archive_ignores_plain_mkv(tmp_path: Path) -> None:
    video = tmp_path / "clip.mkv"
    video.write_bytes(b"video")

    assert classify_archive(video) is None


def test_publication_does_not_write_through_symlinked_paths(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    root = tmp_path / "import"
    root.mkdir()
    real_dir = root / "real"
    real_dir.mkdir()
    alias = root / "alias"
    alias.symlink_to(real_dir)
    _write_zip(archive, {"alias/clip.mkv": b"new-bytes"})
    keeper = real_dir / "keeper.mkv"
    keeper.write_bytes(b"keep-me")

    extract_archive_to_directory(archive, root)

    assert keeper.read_bytes() == b"keep-me"
    assert not (real_dir / "clip.mkv").exists()
    assert payload_file(root, "alias", "clip.mkv").read_bytes() == b"new-bytes"


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


def test_gzip_compression_bomb_capped_by_stream_limit(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.mkv.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(b"X" * 256))

    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        64,
    ):
        with pytest.raises(ArchiveExtractionError, match="expanded-byte limit"):
            extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []
    assert not list(tmp_path.glob(".mm-extract-*"))


def test_zip_metadata_lie_capped_by_bounded_copy(tmp_path: Path) -> None:
    archive = tmp_path / "lie.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("bomb.bin")
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = 1
        zf.writestr(info, b"B" * 128)

    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        32,
    ):
        with pytest.raises(ArchiveExtractionError, match="expanded-byte limit"):
            extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []
    assert not list(tmp_path.glob(".mm-extract-*"))


# ---------------------------------------------------------------------------
# Failure cleanup and publication policy
# ---------------------------------------------------------------------------


def test_failure_leaves_preexisting_destination_files(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    keeper = dest / "keeper.mkv"
    keeper.write_bytes(b"keep-me")
    keeper_inode = keeper.stat().st_ino
    _write_zip(archive, {"../escape.mkv": b"x"})

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert keeper.read_bytes() == b"keep-me"
    assert keeper.stat().st_ino == keeper_inode
    assert container_paths(dest) == []


def test_publication_preserves_existing_flat_files(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    existing = dest / "clip.mkv"
    existing.write_bytes(b"original")
    existing_inode = existing.stat().st_ino
    _write_zip(archive, {"clip.mkv": b"new-bytes"})

    extract_archive_to_directory(archive, dest)

    assert existing.read_bytes() == b"original"
    assert existing.stat().st_ino == existing_inode
    assert payload_file(dest, "clip.mkv").read_bytes() == b"new-bytes"


def test_publication_failure_leaves_no_container(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    keeper = dest / "keeper.mkv"
    keeper.write_bytes(b"keeper")
    keeper_inode = keeper.stat().st_ino
    _write_zip(archive, {"clip.mkv": b"new-bytes"})

    from miramedia.imports import archive_publication as publication

    with (
        patch.object(
            publication,
            "_install_staging_payload",
            side_effect=OSError("simulated rename failure"),
        ),
        pytest.raises(ArchiveExtractionError, match="rename failure"),
    ):
        extract_archive_to_directory(archive, dest)

    assert keeper.read_bytes() == b"keeper"
    assert keeper.stat().st_ino == keeper_inode
    assert container_paths(dest) == []


def test_publication_idempotent_repeat(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"same-bytes"})

    first = extract_archive_to_directory(archive, dest)
    second = extract_archive_to_directory(archive, dest)

    assert first == second
    assert len(container_paths(dest)) == 1
    assert payload_file(dest, "clip.mkv").read_bytes() == b"same-bytes"


def test_staging_mkdir_failure_cleans_up(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})

    from miramedia.imports import archive_extraction as mod

    def _fail_create(_parent_fd: int) -> mod.BoundStagingDirectory:
        msg = "failed to create staging directory"
        raise ArchiveExtractionError(msg)

    with (
        patch.object(mod, "_create_staging_dir", side_effect=_fail_create),
        pytest.raises(ArchiveExtractionError, match="staging directory"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_staging_cleaned_up_after_success(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"ok"})

    from miramedia.imports import archive_extraction as mod

    created: list[Path] = []
    real_create = mod._create_staging_dir
    parent = dest.parent

    def _track(parent_fd: int) -> mod.BoundStagingDirectory:
        staging = real_create(parent_fd)
        created.append(parent / staging.name)
        return staging

    with patch.object(mod, "_create_staging_dir", side_effect=_track):
        extract_archive_to_directory(archive, dest)

    assert created
    assert not created[0].exists()
    assert payload_file(dest, "clip.mkv").read_bytes() == b"ok"


def test_staging_cleaned_up_after_failure(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"../escape.mkv": b"x"})

    from miramedia.imports import archive_extraction as mod

    created: list[Path] = []
    real_create = mod._create_staging_dir
    parent = dest.parent

    def _track(parent_fd: int) -> mod.BoundStagingDirectory:
        staging = real_create(parent_fd)
        created.append(parent / staging.name)
        return staging

    with (
        patch.object(mod, "_create_staging_dir", side_effect=_track),
        pytest.raises(ArchiveExtractionError),
    ):
        extract_archive_to_directory(archive, dest)

    assert created
    assert not created[0].exists()


def test_cleanup_failure_preserves_primary_exception(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"../escape.mkv": b"x"})

    from miramedia.imports import archive_publication as publication

    with (
        patch.object(
            publication,
            "quarantine_owned_directory",
            side_effect=OSError("cleanup failed"),
        ),
        pytest.raises(ArchiveExtractionError, match="traversal"),
    ):
        extract_archive_to_directory(archive, dest)


def test_get_files_for_import_discovers_video_inside_container(tmp_path: Path) -> None:
    from miramedia.imports.files import get_files_for_import

    archive = tmp_path / "release.zip"
    _write_zip(archive, {"movie.mkv": b"video-bytes", "readme.txt": b"notes"})

    video_files, subtitle_files, all_files = get_files_for_import(tmp_path)

    published_video = payload_file(tmp_path, "movie.mkv")
    assert published_video in all_files
    assert video_files == [published_video]
    assert subtitle_files == []


def test_get_files_for_import_extracts_mkv_gz(tmp_path: Path) -> None:
    from miramedia.imports.files import get_files_for_import

    archive = tmp_path / "clip.mkv.gz"
    archive.write_bytes(gzip.compress(b"gz-video"))

    video_files, _subtitle_files, all_files = get_files_for_import(tmp_path)

    published_video = payload_file(tmp_path, "clip.mkv")
    assert published_video.read_bytes() == b"gz-video"
    assert published_video in all_files
    assert video_files == [published_video]


def test_get_files_for_import_skips_unsupported_rar(tmp_path: Path) -> None:
    from miramedia.imports.files import get_files_for_import

    archive = tmp_path / "release.rar"
    archive.write_bytes(b"fake-rar")
    keeper = tmp_path / "keeper.mkv"
    keeper.write_bytes(b"keeper")

    _video_files, subtitle_files, all_files = get_files_for_import(tmp_path)

    assert keeper.read_bytes() == b"keeper"
    assert container_paths(tmp_path) == []
    assert subtitle_files == []
    assert keeper in all_files


def test_policy_constants() -> None:
    assert MAX_ARCHIVE_ENTRIES == 10_000
    assert MAX_EXPANDED_BYTES == 50 * 1024**3

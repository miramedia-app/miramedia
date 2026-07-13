"""Regression tests for final archive extraction audit fixes."""

from __future__ import annotations

import gzip
import os
import struct
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    classify_archive,
    extract_archive_to_directory,
)
from miramedia.imports.files import extract_archives, get_files_for_import
from tests.archive_test_helpers import container_paths, payload_file


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_destination_symlink_rejected_before_publication(tmp_path: Path) -> None:
    root = tmp_path / "import"
    real = tmp_path / "real"
    real.mkdir()
    root.symlink_to(real)
    archive = tmp_path / "release.zip"
    _write_zip(archive, {"clip.mkv": b"x"})

    with pytest.raises(ArchiveExtractionError, match="redirected during bind"):
        extract_archive_to_directory(archive, root)

    assert container_paths(real) == []


def test_bind_directory_rejects_root_swapped_to_symlink(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    root = tmp_path / "import"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()

    real_walk = publication._walk_open_directory

    def _swap_then_walk(parts: tuple[str, ...]) -> int:
        if parts and parts[-1] == "import":
            root.rmdir()
            root.symlink_to(outside)
        return real_walk(parts)

    with (
        patch.object(publication, "_walk_open_directory", side_effect=_swap_then_walk),
        pytest.raises(ArchiveExtractionError, match="redirected"),
    ):
        publication.bind_directory(root)


def test_quarantine_cleanup_leaves_replaced_private(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    parent = tmp_path / "import"
    parent.mkdir()
    private_name = f"{publication.PRIVATE_BUILD_PREFIX}abc"
    private_path = parent / private_name
    private_path.mkdir()
    private_stat = private_path.lstat()
    private_path.rmdir()
    replacement = parent / "replacement"
    replacement.mkdir()
    (replacement / "marker").write_bytes(b"safe")
    replacement.rename(private_path)

    parent_fd = publication.bind_directory(parent)
    try:
        publication.quarantine_owned_directory(
            parent_fd,
            private_name,
            private_stat,
            allow_recursive_cleanup=True,
        )
        assert (private_path / "marker").read_bytes() == b"safe"
        assert list(parent.glob(f"{publication.QUARANTINE_PREFIX}*")) == []
    finally:
        os.close(parent_fd)


def test_zip_preflight_mismatch_skips_zipfile_allocation(tmp_path: Path) -> None:
    archive = tmp_path / "lowcount.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {f"f{i}.txt": b"x" for i in range(3)})
    data = bytearray(archive.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<H", data, eocd + 10, 1)
    struct.pack_into("<H", data, eocd + 12, 1)
    archive.write_bytes(data)

    with (
        patch("miramedia.imports.archive_parsers.zipfile.ZipFile") as zipfile_ctor,
        pytest.raises(ArchiveExtractionError),
    ):
        extract_archive_to_directory(archive, dest)

    zipfile_ctor.assert_not_called()
    assert container_paths(dest) == []


def test_gzip_tar_directory_payload_rejected_under_tiny_limit(tmp_path: Path) -> None:
    header = bytearray(512)
    name = b"dir/"
    header[0 : len(name)] = name
    header[124:136] = f"{4096:o}".encode().rjust(11, b"0") + b"\0"
    header[156:157] = tarfile.DIRTYPE
    chksum, _ = tarfile.calc_chksums(header)
    header[148:156] = f"{chksum:06o}\0 ".encode("ascii")
    plain = header + b"\0" * 4096
    archive = tmp_path / "dirs.tar.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(bytes(plain)))

    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        64,
    ):
        with pytest.raises(
            ArchiveExtractionError, match="directory entry declares payload"
        ):
            extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_staging_mkdir_failure_raises_archive_extraction_error(tmp_path: Path) -> None:
    from miramedia.imports import archive_extraction as mod

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})

    def _fail_create(_parent_fd: int) -> mod.BoundStagingDirectory:
        msg = "failed to create staging directory"
        raise ArchiveExtractionError(msg)

    with (
        patch.object(mod, "_create_staging_dir", side_effect=_fail_create),
        pytest.raises(ArchiveExtractionError, match="staging directory"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


@pytest.mark.parametrize(
    ("filename", "expected_format"),
    [
        ("release.cbr", "rar"),
        ("release.rev", "rar"),
        ("release.cb7", "7z"),
    ],
)
def test_classify_archive_marks_aliases_unsupported(
    tmp_path: Path,
    filename: str,
    expected_format: str,
) -> None:
    archive = tmp_path / filename
    archive.write_bytes(b"fake")

    classification = classify_archive(archive)

    assert classification is not None
    assert classification.disposition == "unsupported"
    assert classification.format == expected_format


def test_get_files_for_import_extracts_raw_gz_and_bz2(tmp_path: Path) -> None:
    import bz2

    gz = tmp_path / "movie.gz"
    bz = tmp_path / "clip.bz2"
    gz.write_bytes(gzip.compress(b"gz"))
    bz.write_bytes(bz2.compress(b"bz"))

    extract_archives([gz, bz])

    assert payload_file(tmp_path, "movie").read_bytes() == b"gz"
    assert payload_file(tmp_path, "clip").read_bytes() == b"bz"


def test_get_files_for_import_skips_cbr_explicitly(tmp_path: Path) -> None:
    archive = tmp_path / "release.cbr"
    archive.write_bytes(b"fake")
    keeper = tmp_path / "keeper.mkv"
    keeper.write_bytes(b"keeper")

    with patch(
        "miramedia.imports.files.extract_archive_to_directory",
    ) as extract_mock:
        get_files_for_import(tmp_path)

    extract_mock.assert_not_called()
    assert keeper.read_bytes() == b"keeper"
    assert container_paths(tmp_path) == []

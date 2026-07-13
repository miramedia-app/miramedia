"""Adversarial archive extraction regressions from security review."""

from __future__ import annotations

import bz2
import gzip
import io
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
from tests.archive_test_helpers import container_paths, payload_file


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _write_zip_dirs(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name if name.endswith("/") else f"{name}/", b"")


def _write_pax_tar(path: Path, *, payload_size: int) -> None:
    header = bytearray(512)
    name = b"././@LongLink"
    header[0 : len(name)] = name
    size_field = f"{payload_size:o}".encode("ascii")
    if len(size_field) > 11:
        size_field = size_field[-11:]
    header[124:136] = size_field.rjust(11, b"0") + b"\0"
    header[156:157] = tarfile.XHDTYPE
    chksum, _ = tarfile.calc_chksums(header)
    header[148:156] = f"{chksum:06o}\0 ".encode("ascii")
    padding = (-(len(header) + payload_size)) % 512
    path.write_bytes(bytes(header) + b"\0" * payload_size + b"\0" * padding)


def _write_oversize_tar_member(path: Path, *, size: int) -> None:
    with tarfile.open(path, "w") as tf:
        info = tarfile.TarInfo("huge.bin")
        info.size = size
        tf.addfile(info, io.BytesIO(b"x" * size))


def _write_forged_directory_tar(path: Path, *, payload_size: int) -> None:
    header = bytearray(512)
    name = b"dir/"
    header[0 : len(name)] = name
    size_field = f"{payload_size:o}".encode("ascii")
    if len(size_field) > 11:
        size_field = size_field[-11:]
    header[124:136] = size_field.rjust(11, b"0") + b"\0"
    header[156:157] = tarfile.DIRTYPE
    chksum, _ = tarfile.calc_chksums(header)
    header[148:156] = f"{chksum:06o}\0 ".encode("ascii")
    path.write_bytes(bytes(header))


def test_tar_rejects_large_pax_header_with_tiny_limit(tmp_path: Path) -> None:
    archive = tmp_path / "pax.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_pax_tar(archive, payload_size=2 * 1024 * 1024)

    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        2048,
    ):
        with pytest.raises(ArchiveExtractionError, match="pax"):
            extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_zip_rejects_five_directory_entries_with_limit_one(tmp_path: Path) -> None:
    archive = tmp_path / "dirs.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip_dirs(archive, [f"d{i}/" for i in range(5)])

    with patch(
        "miramedia.imports.archive_extraction.MAX_ARCHIVE_ENTRIES",
        1,
    ):
        with pytest.raises(ArchiveExtractionError):
            extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_tar_rejects_five_directory_entries_with_limit_one(tmp_path: Path) -> None:
    archive = tmp_path / "dirs.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    with tarfile.open(archive, "w") as tf:
        for i in range(5):
            info = tarfile.TarInfo(f"d{i}/")
            info.type = tarfile.DIRTYPE
            tf.addfile(info)

    with patch(
        "miramedia.imports.archive_extraction.MAX_ARCHIVE_ENTRIES",
        1,
    ):
        with pytest.raises(ArchiveExtractionError):
            extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_tar_rejects_directory_with_payload_plain(tmp_path: Path) -> None:
    archive = tmp_path / "dirpayload.tar"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_forged_directory_tar(archive, payload_size=4096)

    with pytest.raises(
        ArchiveExtractionError, match="directory entry declares payload"
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_tar_rejects_directory_with_payload_compressed(tmp_path: Path) -> None:
    archive = tmp_path / "dirpayload.tar.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    plain = archive.with_suffix(".tar")
    _write_forged_directory_tar(plain, payload_size=4096)
    archive.write_bytes(gzip.compress(plain.read_bytes()))

    with pytest.raises(
        ArchiveExtractionError, match="directory entry declares payload"
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_compressed_tar_rejects_oversize_first_member_before_write(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "big.tar.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_oversize_tar_member(archive.with_suffix(".tar"), size=4096)
    archive.write_bytes(gzip.compress(archive.with_suffix(".tar").read_bytes()))

    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        64,
    ):
        with pytest.raises(ArchiveExtractionError, match="expanded-byte limit"):
            extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_publication_parent_symlink_swap_blocks_root_open(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    root = tmp_path / "import"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "secret.mkv"
    outside_file.write_bytes(b"outside")
    root.mkdir()
    _write_zip(archive, {"clip.mkv": b"new-bytes"})

    from miramedia.imports import archive_publication as publication

    real_publish = publication.publish_staging_tree

    def _swap_destination_then_publish(
        staging: Path,
        destination_dir: Path,
        *,
        destination_stat: os.stat_result,
    ) -> Path:
        root.rmdir()
        root.symlink_to(outside)
        return real_publish(
            staging,
            destination_dir,
            destination_stat=destination_stat,
        )

    with (
        patch.object(
            publication,
            "publish_staging_tree",
            side_effect=_swap_destination_then_publish,
        ),
        pytest.raises(ArchiveExtractionError, match="redirected"),
    ):
        extract_archive_to_directory(archive, root)

    assert outside_file.read_bytes() == b"outside"
    assert container_paths(root) == []


@pytest.mark.parametrize(
    ("archive_name", "payload"),
    [
        ("bad.zip", b"not-a-zip"),
        ("bad.tar", b"not-a-tar"),
        ("bad.gz", b"not-gzip"),
        ("bad.bz2", b"not-bz2"),
    ],
)
def test_malformed_archives_raise_archive_extraction_error(
    tmp_path: Path,
    archive_name: str,
    payload: bytes,
) -> None:
    archive = tmp_path / archive_name
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(payload)

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_gzip_writer_never_exceeds_patched_cap(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(b"x" * 256))

    with patch(
        "miramedia.imports.archive_extraction.MAX_EXPANDED_BYTES",
        64,
    ):
        with pytest.raises(ArchiveExtractionError, match="expanded-byte limit"):
            extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_rar_mime_alias_classified_unsupported(tmp_path: Path) -> None:
    archive = tmp_path / "release.bin"
    archive.write_bytes(b"fake")
    classification = classify_archive(archive.with_suffix(".rar"))
    assert classification is not None
    assert classification.disposition == "unsupported"


def test_clip_mkv_gz_routes_to_gzip_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(b"gz-video"))

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"gz-video"


def test_bz2_single_file_routing(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.bz2"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(bz2.compress(b"bz2-video"))

    extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"bz2-video"


def test_tar_xz_explicitly_rejected(tmp_path: Path) -> None:
    import lzma

    archive = tmp_path / "release.tar.xz"
    dest = tmp_path / "import"
    dest.mkdir()
    with tarfile.open(archive.with_suffix(".tar"), "w") as tf:
        info = tarfile.TarInfo("clip.mkv")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"xyz"))
    archive.write_bytes(lzma.compress(archive.with_suffix(".tar").read_bytes()))

    with pytest.raises(ArchiveExtractionError, match="not supported"):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_zip_rejects_low_eocd_count_with_many_records(tmp_path: Path) -> None:
    archive = tmp_path / "lowcount.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {f"f{i}.txt": b"x" for i in range(4)})
    data = bytearray(archive.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<H", data, eocd + 10, 1)
    struct.pack_into("<H", data, eocd + 12, 1)
    archive.write_bytes(data)

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_zip_rejects_fake_eocd_signature_inside_comment(tmp_path: Path) -> None:
    archive = tmp_path / "fake-eocd.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})
    data = bytearray(archive.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    comment = b"decoy" + b"PK\x05\x06" + b"0000"
    struct.pack_into("<H", data, eocd + 20, len(comment))
    data.extend(comment)
    archive.write_bytes(data)

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_zip_rejects_truncated_central_directory_filename(tmp_path: Path) -> None:
    archive = tmp_path / "truncated.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})
    data = bytearray(archive.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    cd_size = struct.unpack_from("<I", data, eocd + 12)[0]
    struct.pack_into("<I", data, eocd + 12, cd_size - 1)
    archive.write_bytes(data)

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_reserved_windows_names_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "reserved.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"CON.txt": b"x"})

    with pytest.raises(ArchiveExtractionError, match="reserved windows"):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_duplicate_logical_paths_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "dup.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("File.txt", b"one")
        zf.writestr("file.txt", b"two")

    with pytest.raises(ArchiveExtractionError, match="duplicate archive entry path"):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_publication_private_build_failure_leaves_destination_unchanged(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    keeper = dest / "keeper.mkv"
    keeper.write_bytes(b"keeper")
    keeper_inode = keeper.stat().st_ino
    _write_zip(archive, {"clip.mkv": b"x"})

    from miramedia.imports import archive_publication as publication

    with (
        patch.object(
            publication,
            "_install_staging_payload",
            side_effect=ArchiveExtractionError("simulated private build failure"),
        ),
        pytest.raises(ArchiveExtractionError, match="private build failure"),
    ):
        extract_archive_to_directory(archive, dest)

    assert keeper.stat().st_ino == keeper_inode
    assert container_paths(dest) == []

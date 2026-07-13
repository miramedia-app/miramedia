"""Adversarial archive extraction regressions from security review."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    extract_archive_to_directory,
    is_archive_mime,
)


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

    assert list(dest.iterdir()) == []


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

    assert list(dest.iterdir()) == []


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

    assert list(dest.iterdir()) == []


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

    assert list(dest.iterdir()) == []


def test_promotion_parent_symlink_swap_blocks_write(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    root = tmp_path / "import"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "secret.mkv"
    outside_file.write_bytes(b"outside")
    root.mkdir()
    alias = root / "alias"
    alias.symlink_to(outside)
    _write_zip(archive, {"alias/clip.mkv": b"new-bytes"})

    with pytest.raises(ArchiveExtractionError):
        extract_archive_to_directory(archive, root)

    assert outside_file.read_bytes() == b"outside"
    assert not (outside / "clip.mkv").exists()
    assert list(root.iterdir()) == [alias]


def test_staging_replacement_before_staging_unlink_rolls_back(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"first.mkv": b"first-bytes", "second.mkv": b"second-bytes"})

    real_unlink = os.unlink
    staging_unlinks = {"second": 0}

    def _fail_first_staging_second_unlink(
        name: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if name == "second.mkv" and kwargs.get("dir_fd") is not None:
            staging_unlinks["second"] += 1
            if staging_unlinks["second"] == 1:
                (dest / "first.mkv").write_bytes(b"replaced-after-link")
                msg = "simulated staging unlink failure"
                raise OSError(msg)
        real_unlink(name, *args, **kwargs)

    with (
        patch.object(os, "unlink", _fail_first_staging_second_unlink),
        pytest.raises(ArchiveExtractionError, match="staged source"),
    ):
        extract_archive_to_directory(archive, dest)

    assert not (dest / "first.mkv").exists()
    assert not (dest / "second.mkv").exists()


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

    assert list(dest.iterdir()) == []


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

    assert list(dest.iterdir()) == []
    assert not list(dest.glob("**/*"))


def test_rar_mime_alias_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "release.bin"
    archive.write_bytes(b"fake")
    dest = tmp_path / "import"
    dest.mkdir()

    with patch(
        "miramedia.imports.archive_extraction._guess_mime_encoding",
        return_value=("application/vnd.rar", None),
    ):
        assert is_archive_mime("application/vnd.rar")
        with pytest.raises(ArchiveExtractionError, match="not supported"):
            extract_archive_to_directory(archive, dest)

    assert list(dest.iterdir()) == []


def test_clip_mkv_gz_routes_to_gzip_single_file(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.gz"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(gzip.compress(b"gz-video"))

    extract_archive_to_directory(archive, dest)

    assert (dest / "clip.mkv").read_bytes() == b"gz-video"


def test_bz2_single_file_routing(tmp_path: Path) -> None:
    archive = tmp_path / "clip.mkv.bz2"
    dest = tmp_path / "import"
    dest.mkdir()
    archive.write_bytes(bz2.compress(b"bz2-video"))

    extract_archive_to_directory(archive, dest)

    assert (dest / "clip.mkv").read_bytes() == b"bz2-video"


def test_tar_xz_explicitly_rejected(tmp_path: Path) -> None:
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

    assert list(dest.iterdir()) == []


def test_rollback_continues_after_unlink_error(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.mkv", b"first")
        zf.writestr("second.mkv", b"second")

    from miramedia.imports import archive_promotion as promo

    real_unlink_at = promo._unlink_at_if_owned
    calls = {"count": 0}

    def _fail_first_rollback(artifact: promo._PromotedArtifact) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            msg = "simulated rollback unlink failure"
            raise OSError(msg)
        real_unlink_at(artifact)

    with (
        patch.object(promo, "_unlink_at_if_owned", side_effect=_fail_first_rollback),
        patch.object(
            promo,
            "_atomic_link_at",
            side_effect=OSError("simulated promotion failure"),
        ),
        pytest.raises(ArchiveExtractionError, match="promotion failed"),
    ):
        extract_archive_to_directory(archive, dest)

    assert not (dest / "first.mkv").exists()
    assert not (dest / "second.mkv").exists()

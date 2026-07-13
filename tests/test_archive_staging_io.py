"""Descriptor safety and resource tests for archive staging I/O."""

from __future__ import annotations

import errno
import io
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    _ExpandedByteBudget,
    extract_archive_to_directory,
)
from miramedia.imports.archive_publication import bind_directory, staging_content_digest
from miramedia.imports.archive_staging_io import (
    _FdWriter,
    _mkdir_parts_at,
    _open_parent_at,
    mkdir_entry,
    open_entry_for_write,
    require_descriptor_staging_supported,
    write_entry_stream,
)
from tests.archive_test_helpers import payload_file


def _count_open_fds() -> int:
    return len(list(Path("/dev/fd").iterdir()))


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _create_bound_staging(parent: Path):
    from miramedia.imports import archive_extraction as extraction

    parent_fd = bind_directory(parent)
    return extraction._create_staging_dir(parent_fd)


def test_require_descriptor_staging_supported_passes_on_unix() -> None:
    require_descriptor_staging_supported()


def test_open_entry_for_write_closes_leaf_fd(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = _create_bound_staging(parent)
    closed: list[int] = []
    real_close = os.close

    def _track_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    try:
        with patch.object(os, "close", side_effect=_track_close):
            with open_entry_for_write(staging.fd, "clip.mkv") as leaf_fd:
                assert leaf_fd not in closed
        assert leaf_fd in closed
    finally:
        staging.close()


def test_mkdir_parts_at_closes_all_fds_on_success(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = _create_bound_staging(parent)
    closed: list[int] = []
    real_close = os.close

    def _track_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    try:
        with patch.object(os, "close", side_effect=_track_close):
            mkdir_entry(staging.fd, "nested/dir")
        assert staging.fd not in closed
        assert closed
    finally:
        staging.close()


def test_mkdir_parts_at_closes_fds_on_mid_path_failure(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = _create_bound_staging(parent)
    closed: list[int] = []
    real_close = os.close
    real_open = os.open

    def _track_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def _fail_bad_dir_open(path: str, flags: int, /, **kwargs: object) -> int:
        if path == "bad" and flags & os.O_DIRECTORY:
            msg = "simulated open failure"
            raise OSError(errno.EIO, msg)
        return real_open(path, flags, **kwargs)

    try:
        with (
            patch.object(os, "close", side_effect=_track_close),
            patch.object(os, "open", side_effect=_fail_bad_dir_open),
            pytest.raises(ArchiveExtractionError),
        ):
            _mkdir_parts_at(staging.fd, ("only", "bad"), mode=0o755)
        assert staging.fd not in closed
        assert closed
    finally:
        staging.close()


def test_open_parent_at_closes_fds_on_validation_failure(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = _create_bound_staging(parent)
    os.mkdir("trap", dir_fd=staging.fd)
    trap_fd = os.open(
        "trap",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=staging.fd,
    )
    os.close(
        os.open("bad", os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o644, dir_fd=trap_fd)
    )
    os.close(trap_fd)
    closed: list[int] = []
    real_close = os.close

    def _track_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    try:
        with (
            patch.object(os, "close", side_effect=_track_close),
            pytest.raises(ArchiveExtractionError),
        ):
            _open_parent_at(staging.fd, ("trap", "bad"))
        assert staging.fd not in closed
        assert closed
    finally:
        staging.close()


def test_hardlink_at_leaf_rejected_and_outside_bytes_preserved(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    victim = tmp_path / "victim.dat"
    victim.write_bytes(b"ORIGINAL")
    staging = _create_bound_staging(parent)
    try:
        mkdir_entry(staging.fd, "pkg")
        leaf_path = parent / staging.name / "pkg" / "target.dat"
        os.link(victim, leaf_path)
        with (
            pytest.raises(ArchiveExtractionError, match="already exists"),
            open_entry_for_write(staging.fd, "pkg/target.dat"),
        ):
            pass
    finally:
        staging.close()
    assert victim.read_bytes() == b"ORIGINAL"


def test_fd_writer_completes_partial_writes() -> None:
    payload = b"abcdefghij"
    read_fd, write_fd = os.pipe()
    real_write = os.write
    remaining = {"count": 3}

    def _partial_write(fd: int, data: bytes, /) -> int:
        if fd != write_fd:
            return real_write(fd, data)
        if remaining["count"] > 0:
            remaining["count"] -= 1
            return real_write(fd, data[:1])
        return real_write(fd, data)

    try:
        with patch.object(os, "write", side_effect=_partial_write):
            assert _FdWriter(write_fd).write(payload) == len(payload)
        os.close(write_fd)
        assert os.read(read_fd, len(payload) + 1) == payload
    finally:
        os.close(read_fd)


def test_create_staging_dir_mkdir_failure(tmp_path: Path) -> None:
    from miramedia.imports import archive_extraction as extraction
    from miramedia.imports.archive_staging_io import STAGING_DIR_PREFIX

    require_descriptor_staging_supported()
    parent = tmp_path / "parent"
    parent.mkdir()
    parent_fd = bind_directory(parent)
    real_mkdir = os.mkdir

    def _fail_staging_mkdir(name: str, *args: object, **kwargs: object) -> None:
        if str(name).startswith(STAGING_DIR_PREFIX):
            msg = "simulated staging mkdir failure"
            raise OSError(msg)
        real_mkdir(name, *args, **kwargs)

    try:
        with (
            patch.object(os, "mkdir", side_effect=_fail_staging_mkdir),
            pytest.raises(ArchiveExtractionError, match="staging directory"),
        ):
            extraction._create_staging_dir(parent_fd)
    finally:
        os.close(parent_fd)


def test_fd_writer_rejects_zero_write(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = _create_bound_staging(parent)

    try:
        with (
            pytest.raises(ArchiveExtractionError, match="stalled"),
            open_entry_for_write(staging.fd, "zero.bin") as leaf_fd,
            patch.object(os, "write", return_value=0),
        ):
            _FdWriter(leaf_fd).write(b"x")
    finally:
        staging.close()


def test_write_entry_stream_exact_bytes_and_digest(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = _create_bound_staging(parent)
    payload = b"exact-bytes"

    try:
        write_entry_stream(
            staging.fd,
            "exact.bin",
            io.BytesIO(payload),
            budget=_ExpandedByteBudget(),
        )
        staging_path = parent / staging.name
        assert (staging_path / "exact.bin").read_bytes() == payload
        digest = staging_content_digest(staging_path)
        assert digest == staging_content_digest(staging_path)
    finally:
        staging.close()


def test_many_files_do_not_grow_fd_usage(tmp_path: Path) -> None:
    file_count = 256
    archive = tmp_path / "many.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {f"f{i:04d}.bin": b"x" for i in range(file_count)})

    baseline = _count_open_fds()
    extract_archive_to_directory(archive, dest)
    after = _count_open_fds()
    assert after - baseline < 8
    assert len(list(payload_file(dest, "f0000.bin").parent.iterdir())) == file_count


def test_staging_bind_failure_leaves_replacement_directory(tmp_path: Path) -> None:
    from miramedia.imports import archive_extraction as extraction
    from miramedia.imports.archive_staging_io import STAGING_DIR_PREFIX

    parent = tmp_path / "parent"
    parent.mkdir()
    parent_fd = bind_directory(parent)
    replacement = parent / f"{STAGING_DIR_PREFIX}replacement"
    replacement.mkdir()
    (replacement / "marker").write_bytes(b"safe")
    real_open = os.open
    staging_names: list[str] = []

    def _swap_on_staging_open(path: str, flags: int, /, **kwargs: object) -> int:
        if (
            isinstance(path, str)
            and path.startswith(STAGING_DIR_PREFIX)
            and flags & os.O_DIRECTORY
            and kwargs.get("dir_fd") == parent_fd
        ):
            staging_names.append(path)
            replacement.rename(parent / path)
            msg = "simulated staging bind failure"
            raise OSError(errno.ENOENT, msg)
        return real_open(path, flags, **kwargs)

    try:
        with (
            patch.object(os, "open", side_effect=_swap_on_staging_open),
            pytest.raises(ArchiveExtractionError, match="bind staging directory"),
        ):
            extraction._create_staging_dir(parent_fd)
    finally:
        os.close(parent_fd)

    assert staging_names
    assert (parent / staging_names[0] / "marker").read_bytes() == b"safe"


def test_unsupported_primitive_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "O_EXCL", raising=False)
    with pytest.raises(ArchiveExtractionError, match="not available"):
        require_descriptor_staging_supported()

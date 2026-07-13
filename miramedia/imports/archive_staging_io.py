"""Descriptor-relative staging tree operations for archive extraction."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    _enforce_limits,
    _ExpandedByteBudget,
    _Readable,
    _validate_entry_name,
)

STAGING_DIR_PREFIX = ".mm-extract-"


def require_descriptor_staging_supported() -> None:
    """Fail closed when descriptor-relative staging cannot be enforced."""
    if os.name == "nt":
        msg = "descriptor-bound archive extraction is not available on this platform"
        raise ArchiveExtractionError(msg)
    if not hasattr(os, "O_DIRECTORY"):
        msg = "descriptor-bound archive extraction is not available on this platform"
        raise ArchiveExtractionError(msg)


def mkdir_entry(
    staging_fd: int,
    entry_name: str,
    *,
    mode: int = 0o755,
) -> None:
    """Create a directory entry (and parents) relative to ``staging_fd``."""
    _validate_entry_name(entry_name)
    parts = PurePosixPath(entry_name.rstrip("/")).parts
    if not parts:
        msg = f"unsafe archive entry name: {entry_name!r}"
        raise ArchiveExtractionError(msg)
    _mkdir_parts_at(staging_fd, parts, mode=mode)


@contextmanager
def open_entry_for_write(staging_fd: int, entry_name: str) -> Iterator[int]:
    """Open a regular file for writing relative to ``staging_fd``."""
    _validate_entry_name(entry_name)
    parts = PurePosixPath(entry_name).parts
    if not parts:
        msg = f"unsafe archive entry name: {entry_name!r}"
        raise ArchiveExtractionError(msg)
    parent_fd, opened = _open_parent_at(staging_fd, parts[:-1])
    leaf = parts[-1]
    try:
        try:
            fd = os.open(
                leaf,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                mode=0o644,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            msg = f"failed to create archive entry: {entry_name!r}"
            raise ArchiveExtractionError(msg) from exc
        opened_stat = os.fstat(fd)
        if stat.S_ISLNK(opened_stat.st_mode):
            msg = f"archive entry is a symlink: {entry_name!r}"
            raise ArchiveExtractionError(msg)
        if not stat.S_ISREG(opened_stat.st_mode):
            msg = f"archive entry is not a regular file: {entry_name!r}"
            raise ArchiveExtractionError(msg)
        yield fd
    finally:
        for extra_fd in opened:
            os.close(extra_fd)
        if parent_fd != staging_fd:
            os.close(parent_fd)


class _FdWriter:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def write(self, data: bytes, /) -> int:
        return os.write(self._fd, data)


def write_entry_stream(
    staging_fd: int,
    entry_name: str,
    src: _Readable,
    *,
    budget: _ExpandedByteBudget,
) -> None:
    from miramedia.imports.archive_extraction import _copy_stream_bounded

    parent_parts = PurePosixPath(entry_name).parts[:-1]
    if parent_parts:
        mkdir_entry(staging_fd, "/".join(parent_parts))
    with open_entry_for_write(staging_fd, entry_name) as fd:
        _copy_stream_bounded(src, _FdWriter(fd), budget=budget)


def collect_validated_regular_files(staging_fd: int) -> None:
    """Validate extracted entries under ``staging_fd`` without path traversal."""
    file_count = 0
    total_bytes = 0
    for _root, dirs, files, walk_fd in os.fwalk(
        ".",
        dir_fd=staging_fd,
        topdown=True,
    ):
        for name in dirs:
            info = os.stat(name, dir_fd=walk_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                msg = f"extracted symlink is not allowed: {name!r}"
                raise ArchiveExtractionError(msg)
            if not stat.S_ISDIR(info.st_mode):
                msg = f"extracted non-directory entry is not allowed: {name!r}"
                raise ArchiveExtractionError(msg)
        for name in files:
            info = os.stat(name, dir_fd=walk_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                msg = f"extracted symlink is not allowed: {name!r}"
                raise ArchiveExtractionError(msg)
            if not stat.S_ISREG(info.st_mode):
                msg = f"extracted non-regular file is not allowed: {name!r}"
                raise ArchiveExtractionError(msg)
            if info.st_nlink > 1:
                msg = f"extracted hardlink is not allowed: {name!r}"
                raise ArchiveExtractionError(msg)
            file_count += 1
            total_bytes += info.st_size
            _enforce_limits(file_count, total_bytes)


def _assert_directory_component(info: os.stat_result, part: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        msg = f"archive directory component is a symlink: {part!r}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(info.st_mode):
        msg = f"archive directory component is not a directory: {part!r}"
        raise ArchiveExtractionError(msg)


def _mkdir_parts_at(root_fd: int, parts: tuple[str, ...], *, mode: int) -> None:
    current_fd = root_fd
    opened: list[int] = []
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=mode, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                msg = f"failed to create archive directory component: {part!r}"
                raise ArchiveExtractionError(msg) from exc
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                if exc.errno in {errno.ENOTDIR, errno.ELOOP}:
                    msg = "archive entry path was redirected during extraction"
                    raise ArchiveExtractionError(msg) from exc
                msg = f"failed to open archive directory component: {part!r}"
                raise ArchiveExtractionError(msg) from exc
            info = os.fstat(next_fd)
            _assert_directory_component(info, part)
            if current_fd != root_fd:
                opened.append(current_fd)
            current_fd = next_fd
    except Exception:
        for fd in opened:
            os.close(fd)
        raise
    else:
        for fd in opened:
            os.close(fd)


def _open_parent_at(
    root_fd: int,
    parts: tuple[str, ...],
) -> tuple[int, list[int]]:
    if not parts:
        return root_fd, []
    current_fd = root_fd
    opened: list[int] = []
    for part in parts:
        try:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
        except OSError as exc:
            if exc.errno in {errno.ENOTDIR, errno.ELOOP}:
                msg = "archive entry path was redirected during extraction"
                raise ArchiveExtractionError(msg) from exc
            msg = f"failed to open archive directory component: {part!r}"
            raise ArchiveExtractionError(msg) from exc
        info = os.fstat(next_fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            msg = f"archive entry path is not a directory: {part!r}"
            raise ArchiveExtractionError(msg)
        if current_fd != root_fd:
            opened.append(current_fd)
        current_fd = next_fd
    return current_fd, opened

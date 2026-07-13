"""Descriptor-relative staging tree operations for archive extraction."""

from __future__ import annotations

import errno
import inspect
import logging
import os
import stat
import tempfile
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

log = logging.getLogger(__name__)

STAGING_DIR_PREFIX = ".mm-extract-"

_REQUIRED_OPEN_FLAGS = ("O_NOFOLLOW", "O_DIRECTORY", "O_EXCL")
_REQUIRED_DIR_FD_PARAMS = {
    "open": ("dir_fd",),
    "mkdir": ("dir_fd",),
    "stat": ("dir_fd",),
    "rename": ("src_dir_fd", "dst_dir_fd"),
    "rmdir": ("dir_fd",),
    "unlink": ("dir_fd",),
    "fwalk": ("dir_fd",),
}


def require_descriptor_staging_supported() -> None:
    """Fail closed when descriptor-relative staging cannot be enforced."""
    if os.name == "nt":
        msg = "descriptor-bound archive extraction is not available on this platform"
        raise ArchiveExtractionError(msg)
    for flag_name in _REQUIRED_OPEN_FLAGS:
        if not hasattr(os, flag_name):
            msg = (
                "descriptor-bound archive extraction is not available on this platform"
            )
            raise ArchiveExtractionError(msg)
    for func_name, params in _REQUIRED_DIR_FD_PARAMS.items():
        func = getattr(os, func_name, None)
        if func is None:
            msg = (
                "descriptor-bound archive extraction is not available on this platform"
            )
            raise ArchiveExtractionError(msg)
        signature = inspect.signature(func)
        for param in params:
            if param not in signature.parameters:
                msg = "descriptor-bound archive extraction is not available on this platform"
                raise ArchiveExtractionError(msg)
    try:
        _probe_descriptor_operations()
    except OSError as exc:
        msg = "descriptor-bound archive extraction is not available on this platform"
        raise ArchiveExtractionError(msg) from exc


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
    leaf_fd: int | None = None
    try:
        try:
            leaf_fd = os.open(
                leaf,
                _leaf_create_flags(),
                mode=0o644,
                dir_fd=parent_fd,
            )
        except FileExistsError as exc:
            msg = f"archive entry already exists: {entry_name!r}"
            raise ArchiveExtractionError(msg) from exc
        except OSError as exc:
            msg = f"failed to create archive entry: {entry_name!r}"
            raise ArchiveExtractionError(msg) from exc
        _assert_regular_leaf(os.fstat(leaf_fd), entry_name)
        yield leaf_fd
    finally:
        if leaf_fd is not None:
            os.close(leaf_fd)
        for extra_fd in opened:
            os.close(extra_fd)
        if parent_fd != staging_fd:
            os.close(parent_fd)


class _FdWriter:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def write(self, data: bytes, /) -> int:
        total = 0
        view = memoryview(data)
        while total < len(data):
            try:
                written = os.write(self._fd, view[total:])
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                msg = "failed to write archive entry bytes"
                raise ArchiveExtractionError(msg) from exc
            if written == 0:
                msg = "archive write stalled while extracting entry"
                raise ArchiveExtractionError(msg)
            total += written
        return total


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


def _leaf_create_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _assert_regular_leaf(info: os.stat_result, entry_name: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        msg = f"archive entry is a symlink: {entry_name!r}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISREG(info.st_mode):
        msg = f"archive entry is not a regular file: {entry_name!r}"
        raise ArchiveExtractionError(msg)
    if info.st_nlink != 1:
        msg = f"archive entry is not an exclusive regular file: {entry_name!r}"
        raise ArchiveExtractionError(msg)


def _assert_directory_component(info: os.stat_result, part: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        msg = f"archive directory component is a symlink: {part!r}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(info.st_mode):
        msg = f"archive directory component is not a directory: {part!r}"
        raise ArchiveExtractionError(msg)


def _close_fds(fds: list[int]) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


def _mkdir_parts_at(root_fd: int, parts: tuple[str, ...], *, mode: int) -> None:
    opened: list[int] = []
    current_fd = root_fd
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
            try:
                _assert_directory_component(os.fstat(next_fd), part)
            except Exception:
                os.close(next_fd)
                raise
            if current_fd != root_fd:
                opened.append(current_fd)
            current_fd = next_fd
    finally:
        if current_fd != root_fd:
            opened.append(current_fd)
        _close_fds(opened)


def _open_parent_at(
    root_fd: int,
    parts: tuple[str, ...],
) -> tuple[int, list[int]]:
    if not parts:
        return root_fd, []
    opened: list[int] = []
    current_fd = root_fd
    try:
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
            try:
                _assert_directory_component(os.fstat(next_fd), part)
            except Exception:
                os.close(next_fd)
                raise
            if current_fd != root_fd:
                opened.append(current_fd)
            current_fd = next_fd
    except Exception:
        if current_fd != root_fd:
            _close_fds([current_fd])
        _close_fds(opened)
        raise
    else:
        return current_fd, opened


def _probe_descriptor_operations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.mkdir("probe-dir", dir_fd=root_fd)
            dir_fd = os.open(
                "probe-dir",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            try:
                fd = os.open(
                    "file",
                    _leaf_create_flags(),
                    mode=0o644,
                    dir_fd=dir_fd,
                )
                os.close(fd)
                os.stat("file", dir_fd=dir_fd, follow_symlinks=False)
                os.rename("file", "renamed", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.unlink("renamed", dir_fd=dir_fd)
            finally:
                os.close(dir_fd)
            os.rmdir("probe-dir", dir_fd=root_fd)
            next(os.fwalk(".", dir_fd=root_fd))
        finally:
            os.close(root_fd)

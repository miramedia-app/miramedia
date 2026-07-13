"""Publish validated archive staging trees as isolated digest containers."""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import shutil
import stat
from pathlib import Path

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
)

log = logging.getLogger(__name__)

ARCHIVE_CONTAINER_PREFIX = ".mm-archive-"
PAYLOAD_DIR_NAME = "payload"
CONTAINER_DIR_MODE = 0o750
PUBLISHED_FILE_MODE = 0o644
PUBLISHED_DIR_MODE = 0o755


def staging_content_digest(staging: Path) -> str:
    """Return a SHA-256 hex digest of the validated staging tree."""
    digest = hashlib.sha256()
    staging_root = staging.absolute()
    for path in sorted(staging_root.rglob("*")):
        try:
            info = path.lstat()
        except OSError as exc:
            msg = f"failed to digest extracted entry: {path}"
            raise ArchiveExtractionError(msg) from exc
        if not stat.S_ISREG(info.st_mode):
            continue
        rel = path.relative_to(staging_root).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def container_name_for_digest(digest: str) -> str:
    return f"{ARCHIVE_CONTAINER_PREFIX}{digest}"


def publish_staging_tree(
    staging: Path,
    destination_dir: Path,
    *,
    digest: str,
    destination_stat: os.stat_result,
) -> Path:
    """Publish ``staging`` under ``destination_dir`` and return the container path."""
    staging = staging.absolute()
    destination_abs = destination_dir.absolute()
    parent_abs = destination_abs.parent
    container_name = container_name_for_digest(digest)
    container_path = destination_abs / container_name

    parent_fd = bind_directory(parent_abs)
    try:
        destination_fd = open_directory_under(
            parent_fd,
            destination_abs.name,
            destination_stat,
        )
        try:
            existing_fd = _try_open_container(destination_fd, container_name)
            if existing_fd is not None:
                try:
                    if _container_matches_digest(container_path, digest):
                        shutil.rmtree(staging)
                        return container_path
                finally:
                    os.close(existing_fd)
                msg = f"archive container collision: {container_name}"
                raise ArchiveExtractionError(msg)

            reserved_fd, reserved_stat = reserve_container_directory(
                destination_fd,
                container_name,
            )
            try:
                _rename_staging_into_container(
                    staging,
                    parent_fd=parent_fd,
                    container_fd=reserved_fd,
                )
            except Exception:
                remove_reserved_container(
                    destination_fd,
                    container_name,
                    reserved_stat,
                )
                raise
            else:
                os.close(reserved_fd)
                _apply_importable_modes(container_path / PAYLOAD_DIR_NAME)
                os.chmod(container_name, CONTAINER_DIR_MODE, dir_fd=destination_fd)
                return container_path
        finally:
            os.close(destination_fd)
    finally:
        os.close(parent_fd)


def bind_directory(path: Path) -> int:
    """Open ``path`` with ``O_NOFOLLOW`` and verify it matches a fresh ``lstat``."""
    absolute = path.absolute()
    try:
        expected = os.lstat(absolute)
    except OSError as exc:
        msg = f"failed to stat directory: {absolute}"
        raise ArchiveExtractionError(msg) from exc
    if stat.S_ISLNK(expected.st_mode):
        msg = f"directory path is a symlink: {absolute}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(expected.st_mode):
        msg = f"path is not a directory: {absolute}"
        raise ArchiveExtractionError(msg)
    fd = _walk_open_directory(absolute.parts)
    try:
        _assert_opened_directory(fd, expected)
    except Exception:
        os.close(fd)
        raise
    return fd


def open_directory_under(
    parent_fd: int,
    name: str,
    expected_stat: os.stat_result,
) -> int:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        if exc.errno in {errno.ENOTDIR, errno.ELOOP}:
            msg = "directory path was redirected during publication"
            raise ArchiveExtractionError(msg) from exc
        msg = f"failed to open directory component: {name!r}"
        raise ArchiveExtractionError(msg) from exc
    try:
        _assert_opened_directory(fd, expected_stat)
    except Exception:
        os.close(fd)
        raise
    return fd


def reserve_container_directory(
    parent_fd: int,
    container_name: str,
) -> tuple[int, os.stat_result]:
    try:
        os.mkdir(container_name, mode=CONTAINER_DIR_MODE, dir_fd=parent_fd)
    except FileExistsError as exc:
        msg = f"archive container already exists: {container_name}"
        raise ArchiveExtractionError(msg) from exc
    except OSError as exc:
        msg = f"failed to reserve archive container: {container_name}"
        raise ArchiveExtractionError(msg) from exc
    try:
        fd = os.open(
            container_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        remove_reserved_container_on_mkdir_failure(parent_fd, container_name)
        msg = f"failed to open reserved archive container: {container_name}"
        raise ArchiveExtractionError(msg) from exc
    return fd, os.fstat(fd)


def remove_reserved_container(
    parent_fd: int,
    container_name: str,
    reserved_stat: os.stat_result,
) -> None:
    try:
        current = os.stat(
            container_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        msg = f"failed to stat reserved container during cleanup: {container_name}"
        raise ArchiveExtractionError(msg) from exc
    if current.st_ino != reserved_stat.st_ino or current.st_dev != reserved_stat.st_dev:
        msg = "reserved container was replaced before cleanup"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(current.st_mode):
        msg = f"reserved container is not a directory: {container_name}"
        raise ArchiveExtractionError(msg)
    try:
        os.rmdir(container_name, dir_fd=parent_fd)
    except OSError as exc:
        msg = f"failed to remove reserved archive container: {container_name}"
        raise ArchiveExtractionError(msg) from exc


def remove_reserved_container_on_mkdir_failure(
    parent_fd: int,
    container_name: str,
) -> None:
    try:
        os.rmdir(container_name, dir_fd=parent_fd)
    except OSError as exc:
        log.warning(
            "Failed to remove reserved archive container after open failure %s: %s",
            container_name,
            exc,
        )


def _assert_opened_directory(fd: int, expected: os.stat_result) -> None:
    opened = os.fstat(fd)
    if opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino:
        msg = "directory path was redirected during publication"
        raise ArchiveExtractionError(msg)


def _container_matches_digest(container_path: Path, digest: str) -> bool:
    payload = container_path / PAYLOAD_DIR_NAME
    if not payload.is_dir():
        return False
    try:
        payload.lstat()
    except OSError:
        return False
    if payload.is_symlink():
        return False
    try:
        return staging_content_digest(payload) == digest
    except ArchiveExtractionError:
        return False


def _try_open_container(parent_fd: int, container_name: str) -> int | None:
    try:
        return os.open(
            container_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        msg = f"archive container is not accessible: {container_name}"
        raise ArchiveExtractionError(msg) from exc


def _rename_staging_into_container(
    staging: Path,
    *,
    parent_fd: int,
    container_fd: int,
) -> None:
    staging_name = staging.name
    try:
        os.rename(
            staging_name,
            PAYLOAD_DIR_NAME,
            src_dir_fd=parent_fd,
            dst_dir_fd=container_fd,
        )
    except OSError as exc:
        msg = f"failed to publish archive payload: {exc}"
        raise ArchiveExtractionError(msg) from exc


def _apply_importable_modes(payload_root: Path) -> None:
    for path in sorted(payload_root.rglob("*")):
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            continue
        if stat.S_ISDIR(info.st_mode):
            path.chmod(PUBLISHED_DIR_MODE)
        elif stat.S_ISREG(info.st_mode):
            path.chmod(PUBLISHED_FILE_MODE)
    payload_root.chmod(PUBLISHED_DIR_MODE)


def _walk_open_directory(parts: tuple[str, ...]) -> int:
    if not parts:
        msg = "directory path is empty"
        raise ArchiveExtractionError(msg)
    current_fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    opened: list[int] = [current_fd]
    try:
        for part in parts[1:]:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                if exc.errno in {errno.ENOTDIR, errno.ELOOP}:
                    msg = "directory path was redirected during publication"
                    raise ArchiveExtractionError(msg) from exc
                msg = f"failed to open directory component: {part!r}"
                raise ArchiveExtractionError(msg) from exc
            opened.append(next_fd)
            os.close(current_fd)
            opened.pop(0)
            current_fd = next_fd
    except Exception:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    else:
        return current_fd

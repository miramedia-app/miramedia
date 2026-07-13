"""Publish validated archive staging trees as isolated digest containers."""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import secrets
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from miramedia.imports.archive_atomic import atomic_rename_noreplace
from miramedia.imports.archive_extraction import ArchiveExtractionError

log = logging.getLogger(__name__)

ARCHIVE_CONTAINER_PREFIX = ".mm-archive-"
PRIVATE_BUILD_PREFIX = ".mm-publish-"
QUARANTINE_PREFIX = ".mm-quarantine-"
PAYLOAD_DIR_NAME = "payload"
CONTAINER_DIR_MODE = 0o750
PUBLISHED_FILE_MODE = 0o644
PUBLISHED_DIR_MODE = 0o755
_READ_CHUNK_SIZE = 64 * 1024

_ENTRY_DIR = b"D"
_ENTRY_FILE = b"F"
_PATH_LEN = struct.Struct(">I")
_FILE_LEN = struct.Struct(">Q")


class _DigestWriter(Protocol):
    def update(self, data: bytes, /) -> object: ...


@dataclass(frozen=True, slots=True)
class _CanonicalEntry:
    rel_path: str
    entry_type: Literal["dir", "file"]
    file_size: int = 0
    file_stat: os.stat_result | None = None


def staging_content_digest(staging: Path) -> str:
    """Return a canonical SHA-256 hex digest of the validated staging tree."""
    fd = bind_directory(staging)
    try:
        return canonical_tree_digest(fd)
    finally:
        os.close(fd)


def canonical_tree_digest(root_fd: int) -> str:
    """Hash a canonical framed tree rooted at ``root_fd`` without buffering file bytes."""
    digest = hashlib.sha256()
    for entry in _collect_canonical_entry_metadata(root_fd):
        entry_type = _ENTRY_DIR if entry.entry_type == "dir" else _ENTRY_FILE
        digest.update(entry_type)
        path_bytes = entry.rel_path.encode("utf-8")
        digest.update(_PATH_LEN.pack(len(path_bytes)))
        digest.update(path_bytes)
        if entry.entry_type == "file":
            if entry.file_stat is None:
                msg = "canonical tree file entry is missing metadata"
                raise ArchiveExtractionError(msg)
            digest.update(_FILE_LEN.pack(entry.file_size))
            _hash_regular_file_at(
                root_fd,
                entry.rel_path,
                digest,
                entry.file_size,
                entry.file_stat,
            )
    return digest.hexdigest()


def container_name_for_digest(digest: str) -> str:
    return f"{ARCHIVE_CONTAINER_PREFIX}{digest}"


def publish_staging_tree(
    staging: Path,
    destination_dir: Path,
    *,
    destination_stat: os.stat_result,
) -> Path:
    """Publish ``staging`` under ``destination_dir`` and return the container path."""
    staging = staging.absolute()
    destination_abs = destination_dir.absolute()
    parent_abs = destination_abs.parent

    parent_fd = bind_directory(parent_abs)
    try:
        destination_fd = open_directory_under(
            parent_fd,
            destination_abs.name,
            destination_stat,
        )
        try:
            staging_stat = _stat_entry(parent_fd, staging.name)
            private_name = f"{PRIVATE_BUILD_PREFIX}{secrets.token_hex(16)}"
            try:
                os.mkdir(private_name, mode=CONTAINER_DIR_MODE, dir_fd=parent_fd)
            except OSError as exc:
                msg = f"failed to create private archive container: {private_name}"
                raise ArchiveExtractionError(msg) from exc

            private_fd = os.open(
                private_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            private_stat = os.fstat(private_fd)
            published = False
            try:
                _install_staging_payload(
                    parent_fd=parent_fd,
                    staging_name=staging.name,
                    staging_stat=staging_stat,
                    private_fd=private_fd,
                )
                payload_fd = os.open(
                    PAYLOAD_DIR_NAME,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=private_fd,
                )
                try:
                    _verify_installed_payload_identity(
                        private_fd,
                        payload_fd,
                        staging_stat,
                    )
                    _apply_importable_modes_at(payload_fd)
                    digest = canonical_tree_digest(payload_fd)
                finally:
                    os.close(payload_fd)

                container_name = container_name_for_digest(digest)
                container_path = destination_abs / container_name
                os.fchmod(private_fd, CONTAINER_DIR_MODE)

                existing_fd = _try_open_container(destination_fd, container_name)
                if existing_fd is not None:
                    try:
                        if _container_matches_digest_at(existing_fd, digest):
                            return container_path
                    finally:
                        os.close(existing_fd)
                    msg = f"archive container collision: {container_name}"
                    raise ArchiveExtractionError(msg)

                try:
                    atomic_rename_noreplace(
                        parent_fd,
                        private_name,
                        destination_fd,
                        container_name,
                    )
                except FileExistsError as exc:
                    raced_fd = _try_open_container(destination_fd, container_name)
                    if raced_fd is not None:
                        try:
                            if _container_matches_digest_at(raced_fd, digest):
                                return container_path
                        finally:
                            os.close(raced_fd)
                    msg = f"archive container already exists: {container_name}"
                    raise ArchiveExtractionError(msg) from exc
                except OSError as exc:
                    msg = f"failed to publish archive container: {exc}"
                    raise ArchiveExtractionError(msg) from exc
                published = True
                return container_path
            finally:
                os.close(private_fd)
                if not published:
                    _quarantine_private_build(parent_fd, private_name, private_stat)
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


def _install_staging_payload(
    *,
    parent_fd: int,
    staging_name: str,
    staging_stat: os.stat_result,
    private_fd: int,
) -> None:
    _assert_staging_identity(parent_fd, staging_name, staging_stat)
    try:
        os.rename(
            staging_name,
            PAYLOAD_DIR_NAME,
            src_dir_fd=parent_fd,
            dst_dir_fd=private_fd,
        )
    except OSError as exc:
        msg = f"failed to publish archive payload: {exc}"
        raise ArchiveExtractionError(msg) from exc


def _verify_installed_payload_identity(
    _private_fd: int,
    payload_fd: int,
    staging_stat: os.stat_result,
) -> None:
    installed = os.fstat(payload_fd)
    if not stat.S_ISDIR(installed.st_mode):
        msg = "installed archive payload is not a directory"
        raise ArchiveExtractionError(msg)
    if (
        installed.st_dev != staging_stat.st_dev
        or installed.st_ino != staging_stat.st_ino
    ):
        msg = "installed archive payload identity does not match staging"
        raise ArchiveExtractionError(msg)


def _quarantine_private_build(
    parent_fd: int,
    private_name: str,
    private_stat: os.stat_result,
) -> None:
    """Move a private build aside and delete it only when identity is proven."""
    try:
        current = os.stat(
            private_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning(
            "Failed to stat private build %s during cleanup: %s",
            private_name,
            exc,
        )
        return
    if current.st_ino != private_stat.st_ino or current.st_dev != private_stat.st_dev:
        log.warning(
            "Private build %s was replaced before cleanup; leaving it in place",
            private_name,
        )
        return
    if not stat.S_ISDIR(current.st_mode):
        log.warning(
            "Private build %s is no longer a directory; leaving it in place",
            private_name,
        )
        return

    quarantine_name = f"{QUARANTINE_PREFIX}{secrets.token_hex(16)}"
    try:
        os.rename(
            private_name,
            quarantine_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    except OSError as exc:
        log.warning(
            "Failed to quarantine private build %s; leaving it in place: %s",
            private_name,
            exc,
        )
        return

    try:
        quarantined = os.stat(
            quarantine_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        log.warning(
            "Failed to stat quarantined build %s; leaving it in place: %s",
            quarantine_name,
            exc,
        )
        return
    if (
        quarantined.st_ino != private_stat.st_ino
        or quarantined.st_dev != private_stat.st_dev
    ):
        log.warning(
            "Quarantined build %s was replaced; leaving it in place",
            quarantine_name,
        )
        return

    try:
        top_fd = os.open(
            quarantine_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        log.warning(
            "Failed to open quarantined build %s; leaving it in place: %s",
            quarantine_name,
            exc,
        )
        return
    try:
        opened = os.fstat(top_fd)
        if opened.st_ino != private_stat.st_ino or opened.st_dev != private_stat.st_dev:
            log.warning(
                "Quarantined build %s identity changed after open; leaving it",
                quarantine_name,
            )
            return
        _rmtree_at_fd(top_fd)
    except OSError as exc:
        log.warning(
            "Failed to clean quarantined build %s; leaving it in place: %s",
            quarantine_name,
            exc,
        )
        return
    finally:
        os.close(top_fd)
    try:
        os.rmdir(quarantine_name, dir_fd=parent_fd)
    except OSError as exc:
        log.warning(
            "Failed to remove empty quarantine directory %s; leaving it: %s",
            quarantine_name,
            exc,
        )


def _rmtree_at_fd(top_fd: int) -> None:
    for _root, dirs, files, walk_fd in os.fwalk(
        ".",
        dir_fd=top_fd,
        topdown=False,
    ):
        for name in files:
            os.unlink(name, dir_fd=walk_fd)
        for name in dirs:
            os.rmdir(name, dir_fd=walk_fd)


def _assert_opened_directory(fd: int, expected: os.stat_result) -> None:
    opened = os.fstat(fd)
    if opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino:
        msg = "directory path was redirected during publication"
        raise ArchiveExtractionError(msg)


def _assert_staging_identity(
    parent_fd: int,
    staging_name: str,
    staging_stat: os.stat_result,
) -> None:
    current = _stat_entry(parent_fd, staging_name)
    if current.st_dev != staging_stat.st_dev or current.st_ino != staging_stat.st_ino:
        msg = "staging directory was replaced before publication"
        raise ArchiveExtractionError(msg)


def _stat_entry(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        msg = f"failed to stat directory entry: {name!r}"
        raise ArchiveExtractionError(msg) from exc


def _container_matches_digest_at(container_fd: int, digest: str) -> bool:
    try:
        payload_fd = os.open(
            PAYLOAD_DIR_NAME,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=container_fd,
        )
    except OSError:
        return False
    try:
        try:
            return canonical_tree_digest(payload_fd) == digest
        except ArchiveExtractionError:
            return False
    finally:
        os.close(payload_fd)


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


def _collect_canonical_entry_metadata(root_fd: int) -> list[_CanonicalEntry]:
    entries: list[_CanonicalEntry] = []
    for dirpath, dirnames, filenames, walk_fd in os.fwalk(
        ".",
        dir_fd=root_fd,
        topdown=True,
    ):
        rel_base = "" if dirpath == "." else dirpath.removeprefix("./")
        for name in sorted(dirnames):
            rel = name if not rel_base else f"{rel_base}/{name}"
            _validate_directory_entry(walk_fd, name)
            entries.append(_CanonicalEntry(rel, "dir"))
        for name in sorted(filenames):
            rel = name if not rel_base else f"{rel_base}/{name}"
            file_stat = _validate_regular_file_entry(walk_fd, name)
            entries.append(
                _CanonicalEntry(
                    rel,
                    "file",
                    file_size=file_stat.st_size,
                    file_stat=file_stat,
                ),
            )
    entries.sort(key=lambda item: item.rel_path)
    return entries


def _validate_directory_entry(dir_fd: int, name: str) -> None:
    expected = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISLNK(expected.st_mode):
        msg = f"archive tree contains symlink entry: {name!r}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(expected.st_mode):
        msg = f"archive tree contains unsupported entry type: {name!r}"
        raise ArchiveExtractionError(msg)


def _validate_regular_file_entry(dir_fd: int, name: str) -> os.stat_result:
    expected = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    _assert_regular_file_stat(expected, name)
    return expected


def _assert_regular_file_stat(info: os.stat_result, name: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        msg = f"archive tree contains symlink entry: {name!r}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISREG(info.st_mode):
        msg = f"archive tree contains unsupported entry type: {name!r}"
        raise ArchiveExtractionError(msg)
    if info.st_nlink > 1:
        msg = f"archive tree contains hard link entry: {name!r}"
        raise ArchiveExtractionError(msg)


def _open_regular_file_verified(dir_fd: int, name: str) -> tuple[int, os.stat_result]:
    expected = _validate_regular_file_entry(dir_fd, name)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as exc:
        msg = f"failed to read archive tree entry: {name!r}"
        raise ArchiveExtractionError(msg) from exc
    try:
        opened = os.fstat(fd)
        _assert_matching_stat(opened, expected)
        _assert_regular_file_stat(opened, name)
    except Exception:
        os.close(fd)
        raise
    return fd, expected


def _open_directory_verified(dir_fd: int, name: str) -> tuple[int, os.stat_result]:
    expected = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISLNK(expected.st_mode):
        msg = f"archive tree contains symlink entry: {name!r}"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(expected.st_mode):
        msg = f"archive tree contains unsupported entry type: {name!r}"
        raise ArchiveExtractionError(msg)
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=dir_fd,
        )
    except OSError as exc:
        msg = f"failed to open archive tree entry: {name!r}"
        raise ArchiveExtractionError(msg) from exc
    try:
        opened = os.fstat(fd)
        _assert_matching_stat(opened, expected)
    except Exception:
        os.close(fd)
        raise
    return fd, expected


def _assert_matching_stat(opened: os.stat_result, expected: os.stat_result) -> None:
    if opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino:
        msg = "archive tree entry was redirected during validation"
        raise ArchiveExtractionError(msg)
    if stat.S_ISREG(opened.st_mode) and opened.st_nlink > 1:
        msg = "archive tree contains hard link entry"
        raise ArchiveExtractionError(msg)


def _open_path_at(
    root_fd: int, rel_path: str, *, directory: bool
) -> tuple[int, list[int]]:
    parts = rel_path.split("/")
    opened: list[int] = []
    current_fd = root_fd
    try:
        for part in parts[:-1]:
            next_fd, _ = _open_directory_verified(current_fd, part)
            if current_fd != root_fd:
                opened.append(current_fd)
            current_fd = next_fd
        name = parts[-1]
        if directory:
            entry_fd, _ = _open_directory_verified(current_fd, name)
        else:
            entry_fd, _ = _open_regular_file_verified(current_fd, name)
        if current_fd != root_fd:
            opened.append(current_fd)
    except Exception:
        for fd in opened:
            os.close(fd)
        raise
    else:
        return entry_fd, opened


def _hash_regular_file_at(
    root_fd: int,
    rel_path: str,
    digest: _DigestWriter,
    expected_size: int,
    expected_stat: os.stat_result,
) -> None:
    file_fd, opened_fds = _open_path_at(root_fd, rel_path, directory=False)
    hashed = 0
    try:
        opened = os.fstat(file_fd)
        _assert_matching_stat(opened, expected_stat)
        _assert_regular_file_stat(opened, rel_path)
        if opened.st_size != expected_size:
            msg = f"archive tree file size changed: {rel_path!r}"
            raise ArchiveExtractionError(msg)
        while hashed < expected_size:
            chunk = os.read(file_fd, min(_READ_CHUNK_SIZE, expected_size - hashed))
            if not chunk:
                msg = f"archive tree file ended early: {rel_path!r}"
                raise ArchiveExtractionError(msg)
            digest.update(chunk)
            hashed += len(chunk)
        if os.read(file_fd, 1):
            msg = f"archive tree file exceeds declared size: {rel_path!r}"
            raise ArchiveExtractionError(msg)
    finally:
        os.close(file_fd)
        for fd in opened_fds:
            os.close(fd)


def _apply_importable_modes_at(payload_fd: int) -> None:
    for _dirpath, dirnames, filenames, walk_fd in os.fwalk(
        ".",
        dir_fd=payload_fd,
        topdown=False,
    ):
        for name in filenames:
            _fchmod_entry(walk_fd, name, PUBLISHED_FILE_MODE)
        for name in dirnames:
            _fchmod_entry(walk_fd, name, PUBLISHED_DIR_MODE)
    os.fchmod(payload_fd, PUBLISHED_DIR_MODE)


def _fchmod_entry(dir_fd: int, name: str, mode: int) -> None:
    expected = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISLNK(expected.st_mode):
        msg = f"archive payload contains symlink entry: {name!r}"
        raise ArchiveExtractionError(msg)
    open_flags = os.O_RDONLY | os.O_NOFOLLOW
    if stat.S_ISDIR(expected.st_mode):
        open_flags |= os.O_DIRECTORY
    elif not stat.S_ISREG(expected.st_mode):
        msg = f"archive payload contains unsupported entry type: {name!r}"
        raise ArchiveExtractionError(msg)
    try:
        fd = os.open(name, open_flags, dir_fd=dir_fd)
    except OSError as exc:
        msg = f"failed to chmod archive payload entry: {name!r}"
        raise ArchiveExtractionError(msg) from exc
    try:
        opened = os.fstat(fd)
        _assert_matching_stat(opened, expected)
        if stat.S_ISREG(opened.st_mode) and opened.st_nlink > 1:
            msg = f"archive payload contains hard link entry: {name!r}"
            raise ArchiveExtractionError(msg)
        os.fchmod(fd, mode)
    except OSError as exc:
        msg = f"failed to chmod archive payload entry: {name!r}"
        raise ArchiveExtractionError(msg) from exc
    finally:
        os.close(fd)


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

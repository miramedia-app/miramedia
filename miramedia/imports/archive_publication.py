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
COMPLETION_MARKER_PREFIX = ".mm-complete-"
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
class BoundImportDestination:
    """Destination import directory opened relative to a bound parent fd."""

    parent_fd: int
    destination_fd: int
    destination_name: str
    destination_path: Path
    destination_stat: os.stat_result

    def close(self) -> None:
        os.close(self.destination_fd)
        os.close(self.parent_fd)


@dataclass(frozen=True, slots=True)
class BoundStagingDirectory:
    """Staging directory opened and inode-bound at creation time."""

    name: str
    parent_fd: int
    fd: int
    stat: os.stat_result

    def close(self) -> None:
        os.close(self.fd)

    def assert_identity(self) -> None:
        opened = os.fstat(self.fd)
        if opened.st_dev != self.stat.st_dev or opened.st_ino != self.stat.st_ino:
            msg = "staging directory identity changed"
            raise ArchiveExtractionError(msg)


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


def completion_marker_name(digest: str) -> str:
    return f"{COMPLETION_MARKER_PREFIX}{digest}"


def container_digest_from_name(name: str) -> str | None:
    if not name.startswith(ARCHIVE_CONTAINER_PREFIX):
        return None
    digest = name.removeprefix(ARCHIVE_CONTAINER_PREFIX)
    if len(digest) != 64:
        return None
    try:
        int(digest, 16)
    except ValueError:
        return None
    return digest


def is_internal_import_debris(name: str) -> bool:
    return name.startswith(
        (
            PRIVATE_BUILD_PREFIX,
            QUARANTINE_PREFIX,
            ".mm-extract-",
        ),
    )


def is_complete_archive_container(container: Path) -> bool:
    digest = container_digest_from_name(container.name)
    if digest is None or not container.is_dir():
        return False
    marker = container / completion_marker_name(digest)
    if not marker.is_file():
        return False
    try:
        return marker.read_text(encoding="ascii").strip() == digest
    except OSError:
        return False


def list_importable_files(root: Path) -> list[Path]:
    """List importable files under ``root``, honoring archive container policy."""
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        log.warning("Failed to list import directory %s: %s", root, exc)
        return []
    complete_containers = [
        entry
        for entry in entries
        if entry.is_dir() and is_complete_archive_container(entry)
    ]
    if complete_containers:
        results: list[Path] = []
        for container in sorted(complete_containers, key=lambda entry: entry.name):
            payload = container / PAYLOAD_DIR_NAME
            if payload.is_dir():
                _collect_importable_files(payload, results)
        return results
    results: list[Path] = []
    _collect_importable_files(root, results)
    return results


def _collect_importable_files(directory: Path, results: list[Path]) -> None:
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        log.warning("Failed to list import directory %s: %s", directory, exc)
        return
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                name = entry.name
                if is_internal_import_debris(name):
                    continue
                digest = container_digest_from_name(name)
                if digest is not None:
                    if is_complete_archive_container(entry):
                        payload = entry / PAYLOAD_DIR_NAME
                        if payload.is_dir():
                            _collect_importable_files(payload, results)
                    else:
                        log.warning(
                            "Ignoring incomplete archive container: %s",
                            entry,
                        )
                    continue
                _collect_importable_files(entry, results)
            elif entry.is_file():
                results.append(entry)
        except OSError as exc:
            log.warning("Failed to inspect import entry %s: %s", entry, exc)


def publish_staging_tree(
    staging: BoundStagingDirectory,
    destination: BoundImportDestination,
) -> Path:
    """Publish ``staging`` under ``destination`` and return the container path."""
    staging.assert_identity()
    if staging.parent_fd != destination.parent_fd:
        msg = "staging parent fd does not match bound import destination"
        raise ArchiveExtractionError(msg)

    parent_fd = destination.parent_fd
    destination_fd = destination.destination_fd
    destination_abs = destination.destination_path
    staging_stat = staging.stat
    staging_name = staging.name
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
    container_installed = False
    installed_staging = False
    payload_verified = False
    digest = ""
    container_name = ""
    try:
        _install_staging_payload(
            parent_fd=parent_fd,
            staging_name=staging_name,
            staging_stat=staging_stat,
            private_fd=private_fd,
        )
        installed_staging = True
        payload_fd = os.open(
            PAYLOAD_DIR_NAME,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=private_fd,
        )
        try:
            _verify_installed_payload_identity(payload_fd, staging_stat)
            payload_verified = True
            _apply_importable_modes_at(payload_fd)
            digest = canonical_tree_digest(payload_fd)
        finally:
            os.close(payload_fd)

        container_name = container_name_for_digest(digest)
        container_path = destination_abs / container_name
        _write_completion_marker(private_fd, digest)
        os.fchmod(private_fd, CONTAINER_DIR_MODE)

        existing_fd = _try_open_container(destination_fd, container_name)
        if existing_fd is not None:
            try:
                if _published_container_matches(existing_fd, digest):
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
            container_installed = True
        except FileExistsError as exc:
            raced_fd = _try_open_container(destination_fd, container_name)
            if raced_fd is not None:
                try:
                    if _published_container_matches(raced_fd, digest):
                        return container_path
                finally:
                    os.close(raced_fd)
            msg = f"archive container already exists: {container_name}"
            raise ArchiveExtractionError(msg) from exc
        except OSError as exc:
            msg = f"failed to publish archive container: {exc}"
            raise ArchiveExtractionError(msg) from exc

        published_fd = _try_open_container(destination_fd, container_name)
        if published_fd is None:
            msg = f"published archive container is missing: {container_name}"
            raise ArchiveExtractionError(msg)
        try:
            _verify_published_container_identity(
                published_fd,
                private_stat,
                digest,
            )
        finally:
            os.close(published_fd)
        return container_path
    finally:
        # Re-stat through the still-open fd: installing the payload and the
        # fchmod above bump the container's ctime, so private_stat is stale and
        # would read as a replacement to same_inode_generation().
        current_private_stat = os.fstat(private_fd)
        os.close(private_fd)
        if not container_installed:
            allow_recursive_cleanup = (not installed_staging) or payload_verified
            quarantine_owned_directory(
                parent_fd,
                private_name,
                current_private_stat,
                allow_recursive_cleanup=allow_recursive_cleanup,
            )


def assert_matching_directory_stat(
    opened: os.stat_result,
    expected: os.stat_result,
    label: str,
) -> None:
    _assert_matching_directory_stat(opened, expected, label)


def directory_fd_is_empty(dir_fd: int) -> bool:
    return _directory_fd_is_empty(dir_fd)


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


def open_bound_import_destination(destination_dir: Path) -> BoundImportDestination:
    """Bind the parent directory, then open the destination relative to it."""
    destination_dir = Path(destination_dir)
    parent_path = destination_dir.parent
    parent_fd = bind_directory(parent_path)
    destination_name = destination_dir.name
    try:
        destination_fd = os.open(
            destination_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        os.close(parent_fd)
        if exc.errno in {errno.ENOTDIR, errno.ELOOP}:
            msg = "destination path was redirected during bind"
            raise ArchiveExtractionError(msg) from exc
        msg = f"destination is not accessible: {destination_dir}"
        raise ArchiveExtractionError(msg) from exc
    try:
        destination_stat = os.fstat(destination_fd)
        _assert_bound_directory_stat(destination_stat, "destination")
    except Exception:
        os.close(destination_fd)
        os.close(parent_fd)
        raise
    else:
        return BoundImportDestination(
            parent_fd=parent_fd,
            destination_fd=destination_fd,
            destination_name=destination_name,
            destination_path=destination_dir.absolute(),
            destination_stat=destination_stat,
        )


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


def _write_completion_marker(private_fd: int, digest: str) -> None:
    marker_name = completion_marker_name(digest)
    marker_fd = os.open(
        marker_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode=0o644,
        dir_fd=private_fd,
    )
    try:
        payload = digest.encode("ascii")
        written = os.write(marker_fd, payload)
        if written != len(payload):
            msg = "failed to write archive completion marker"
            raise ArchiveExtractionError(msg)
    finally:
        os.close(marker_fd)


def _verify_published_container_identity(
    container_fd: int,
    expected_stat: os.stat_result,
    digest: str,
) -> None:
    opened = os.fstat(container_fd)
    if opened.st_dev != expected_stat.st_dev or opened.st_ino != expected_stat.st_ino:
        msg = "published archive container identity mismatch"
        raise ArchiveExtractionError(msg)
    _verify_container_layout_at(container_fd, digest)


def _published_container_matches(
    container_fd: int,
    digest: str,
    *,
    expected_private_stat: os.stat_result | None = None,
) -> bool:
    try:
        if expected_private_stat is not None:
            opened = os.fstat(container_fd)
            if (
                opened.st_dev != expected_private_stat.st_dev
                or opened.st_ino != expected_private_stat.st_ino
            ):
                return False
        _verify_container_layout_at(container_fd, digest)
    except ArchiveExtractionError:
        return False
    else:
        return True


def _verify_container_layout_at(container_fd: int, digest: str) -> None:
    try:
        payload_fd = os.open(
            PAYLOAD_DIR_NAME,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=container_fd,
        )
    except OSError as exc:
        msg = "published archive payload is missing"
        raise ArchiveExtractionError(msg) from exc
    try:
        payload_stat = os.fstat(payload_fd)
        if not stat.S_ISDIR(payload_stat.st_mode):
            msg = "published archive payload is not a directory"
            raise ArchiveExtractionError(msg)
    finally:
        os.close(payload_fd)

    marker_name = completion_marker_name(digest)
    try:
        marker_fd = os.open(
            marker_name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=container_fd,
        )
    except OSError as exc:
        msg = "published archive completion marker is missing"
        raise ArchiveExtractionError(msg) from exc
    try:
        marker_stat = os.fstat(marker_fd)
        if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_nlink != 1:
            msg = "published archive completion marker is invalid"
            raise ArchiveExtractionError(msg)
        if marker_stat.st_size != len(digest):
            msg = "published archive completion marker has unexpected size"
            raise ArchiveExtractionError(msg)
        payload = os.read(marker_fd, len(digest) + 1)
        if payload.decode("ascii") != digest:
            msg = "published archive completion marker digest mismatch"
            raise ArchiveExtractionError(msg)
    finally:
        os.close(marker_fd)


def _assert_bound_directory_stat(info: os.stat_result, label: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        msg = f"{label} is a symlink"
        raise ArchiveExtractionError(msg)
    if not stat.S_ISDIR(info.st_mode):
        msg = f"{label} is not a directory"
        raise ArchiveExtractionError(msg)


def _assert_matching_directory_stat(
    opened: os.stat_result,
    expected: os.stat_result,
    label: str,
) -> None:
    if opened.st_dev != expected.st_dev or opened.st_ino != expected.st_ino:
        msg = f"{label} identity mismatch"
        raise ArchiveExtractionError(msg)
    _assert_bound_directory_stat(opened, label)


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


def quarantine_owned_directory(
    parent_fd: int,
    name: str,
    expected_stat: os.stat_result,
    *,
    allow_recursive_cleanup: bool,
) -> None:
    """Quarantine ``name`` and optionally delete it when identity is proven."""
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("Failed to stat %s during cleanup: %s", name, exc)
        return
    if not same_inode_generation(current, expected_stat):
        log.warning("%s was replaced before cleanup; leaving it in place", name)
        return
    if not stat.S_ISDIR(current.st_mode):
        log.warning("%s is no longer a directory; leaving it in place", name)
        return

    try:
        pre_rename = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        log.warning(
            "Failed to re-stat %s before quarantine rename; leaving it: %s",
            name,
            exc,
        )
        return
    if not same_inode_generation(pre_rename, expected_stat):
        log.warning("%s changed before quarantine rename; leaving it in place", name)
        return

    quarantine_name = f"{QUARANTINE_PREFIX}{secrets.token_hex(16)}"
    try:
        atomic_rename_noreplace(
            parent_fd,
            name,
            parent_fd,
            quarantine_name,
        )
    except OSError as exc:
        log.warning(
            "Failed to quarantine %s; leaving it in place: %s",
            name,
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
            "Failed to stat quarantined directory %s; leaving it: %s",
            quarantine_name,
            exc,
        )
        return
    if (
        quarantined.st_ino != expected_stat.st_ino
        or quarantined.st_dev != expected_stat.st_dev
    ):
        log.warning(
            "Quarantined directory %s was replaced; leaving it in place",
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
            "Failed to open quarantined directory %s; leaving it: %s",
            quarantine_name,
            exc,
        )
        return
    try:
        opened = os.fstat(top_fd)
        if (
            opened.st_ino != expected_stat.st_ino
            or opened.st_dev != expected_stat.st_dev
        ):
            log.warning(
                "Quarantined directory %s identity changed after open; leaving it",
                quarantine_name,
            )
            return
        if allow_recursive_cleanup:
            _rmtree_at_fd(top_fd)
        if not _directory_fd_is_empty(top_fd):
            log.warning(
                "Quarantine directory %s is not empty; leaving it in place",
                quarantine_name,
            )
            return
        final_opened = os.fstat(top_fd)
        if (
            final_opened.st_ino != expected_stat.st_ino
            or final_opened.st_dev != expected_stat.st_dev
        ):
            log.warning(
                "Quarantine directory %s identity changed before removal; leaving it",
                quarantine_name,
            )
            return
        try:
            _rmdir_open_directory(top_fd)
        except OSError as exc:
            log.warning(
                "Failed to remove empty quarantine directory %s; leaving it: %s",
                quarantine_name,
                exc,
            )
    except OSError as exc:
        log.warning(
            "Failed to clean quarantined directory %s; leaving it: %s",
            quarantine_name,
            exc,
        )
    finally:
        os.close(top_fd)


def _directory_fd_is_empty(dir_fd: int) -> bool:
    for _root, dirs, files, _walk_fd in os.fwalk(".", dir_fd=dir_fd):
        if dirs or files:
            return False
    return True


def _rmdir_open_directory(dir_fd: int) -> None:
    os.rmdir(".", dir_fd=dir_fd)


def _rmtree_at_fd(top_fd: int) -> None:
    for _root, dirs, files, walk_fd in os.fwalk(
        ".",
        dir_fd=top_fd,
        topdown=False,
    ):
        for name in files:
            _unlink_owned_entry(walk_fd, name)
        for name in dirs:
            _rmdir_owned_entry(walk_fd, name)


def _unlink_owned_entry(dir_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        log.warning("Failed to stat %s during cleanup; leaving it: %s", name, exc)
        return
    if stat.S_ISLNK(info.st_mode):
        log.warning("Skipping symlink during quarantine cleanup: %s", name)
        return
    if not stat.S_ISREG(info.st_mode):
        log.warning("Skipping non-regular file during quarantine cleanup: %s", name)
        return
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError as exc:
        log.warning("Failed to unlink %s during cleanup; leaving it: %s", name, exc)


def _rmdir_owned_entry(dir_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        log.warning("Failed to stat %s during cleanup; leaving it: %s", name, exc)
        return
    if stat.S_ISLNK(info.st_mode):
        log.warning("Skipping symlink directory during quarantine cleanup: %s", name)
        return
    if not stat.S_ISDIR(info.st_mode):
        log.warning("Skipping non-directory during quarantine cleanup: %s", name)
        return
    try:
        os.rmdir(name, dir_fd=dir_fd)
    except OSError as exc:
        log.warning("Failed to rmdir %s during cleanup; leaving it: %s", name, exc)


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


def same_inode_generation(opened: os.stat_result, expected: os.stat_result) -> bool:
    """Report whether two stats name the same object, resisting inode reuse.

    Linux recycles inode numbers immediately, so an unlink/recreate swap can
    reproduce (st_dev, st_ino). A recreated inode always carries a fresh ctime,
    so it is the field that distinguishes the original from a replacement. Only
    use this where the object is not legitimately mutated in the compared
    window: any chmod, rename, or child write bumps ctime on the original too.
    """
    return (
        opened.st_dev == expected.st_dev
        and opened.st_ino == expected.st_ino
        and opened.st_ctime_ns == expected.st_ctime_ns
    )


def _assert_matching_stat(opened: os.stat_result, expected: os.stat_result) -> None:
    if not same_inode_generation(opened, expected):
        msg = "archive tree entry was redirected during validation"
        raise ArchiveExtractionError(msg)
    if stat.S_ISREG(opened.st_mode) and opened.st_nlink > 1:
        msg = "archive tree contains hard link entry"
        raise ArchiveExtractionError(msg)


def _assert_file_stat_stable(
    before: os.stat_result,
    after: os.stat_result,
    rel_path: str,
) -> None:
    checks = (
        ("device", before.st_dev, after.st_dev),
        ("inode", before.st_ino, after.st_ino),
        ("mode", before.st_mode, after.st_mode),
        ("link count", before.st_nlink, after.st_nlink),
        ("size", before.st_size, after.st_size),
        ("mtime", before.st_mtime, after.st_mtime),
        ("ctime", before.st_ctime, after.st_ctime),
    )
    for label, expected, actual in checks:
        if expected != actual:
            msg = f"archive tree file {label} changed during hashing: {rel_path!r}"
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
        after = os.fstat(file_fd)
        _assert_file_stat_stable(expected_stat, after, rel_path)
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

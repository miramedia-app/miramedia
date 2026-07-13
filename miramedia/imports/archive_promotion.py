"""Dirfd-based archive promotion with post-link identity tracking."""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    _assert_contained,
    _validate_entry_name,
)

log = logging.getLogger(__name__)


class _PromotedArtifact(NamedTuple):
    parent_fd: int
    name: str
    st_ino: int
    st_dev: int


class _CreatedDirectory(NamedTuple):
    parent_fd: int
    name: str


class _PromotionSession:
    def __init__(self, destination_root: Path, staging_root: Path) -> None:
        self.destination_root = destination_root.resolve()
        self.staging_root = staging_root.resolve()
        self.destination_fd = os.open(
            self.destination_root,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        self.staging_fd = os.open(
            self.staging_root,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        self.promoted: list[_PromotedArtifact] = []
        self.created_dirs: list[_CreatedDirectory] = []
        self._dir_cache: dict[tuple[str, ...], int] = {(): self.destination_fd}

    def close(self) -> None:
        for fd in set(self._dir_cache.values()):
            if fd not in {self.destination_fd, self.staging_fd}:
                os.close(fd)
        os.close(self.staging_fd)
        os.close(self.destination_fd)

    def promote(self, _src: Path, rel_parts: tuple[str, ...]) -> None:
        if not rel_parts:
            msg = "promotion path is empty"
            raise ArchiveExtractionError(msg)
        dst_parent_fd = self._ensure_directory_path(rel_parts[:-1])
        dst_name = rel_parts[-1]
        _validate_entry_name(dst_name)
        src_parent_fd = self._open_directory_under(self.staging_fd, rel_parts[:-1])
        try:
            self.promoted.append(
                _atomic_link_at(
                    src_parent_fd,
                    rel_parts[-1],
                    dst_parent_fd,
                    dst_name,
                ),
            )
        finally:
            if src_parent_fd != self.staging_fd:
                os.close(src_parent_fd)

    def _open_directory_under(self, parent_fd: int, parts: tuple[str, ...]) -> int:
        current_fd = parent_fd
        opened: list[int] = []
        for part in parts:
            _validate_entry_name(part)
            child_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            if current_fd != parent_fd:
                opened.append(current_fd)
            current_fd = child_fd
        for fd in opened:
            os.close(fd)
        return current_fd

    def rollback(self) -> list[OSError]:
        errors: list[OSError] = []
        for artifact in reversed(self.promoted):
            try:
                _unlink_at_if_owned(artifact)
            except OSError as exc:
                errors.append(exc)
                log.warning(
                    "Failed to roll back promoted file %s in dir fd %d: %s",
                    artifact.name,
                    artifact.parent_fd,
                    exc,
                )
        for created in reversed(self.created_dirs):
            try:
                os.rmdir(created.name, dir_fd=created.parent_fd)
            except OSError as exc:
                errors.append(exc)
                log.warning(
                    "Failed to roll back created directory %s in dir fd %d: %s",
                    created.name,
                    created.parent_fd,
                    exc,
                )
        return errors

    def _ensure_directory_path(self, parts: tuple[str, ...]) -> int:
        if not parts:
            return self.destination_fd
        cached = self._dir_cache.get(parts)
        if cached is not None:
            return cached
        parent_fd = self._ensure_directory_path(parts[:-1])
        name = parts[-1]
        _validate_entry_name(name)
        try:
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            os.mkdir(name, dir_fd=parent_fd)
            self.created_dirs.append(_CreatedDirectory(parent_fd, name))
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            msg = f"promotion parent is not a directory: {name}"
            raise ArchiveExtractionError(msg) from exc
        self._dir_cache[parts] = child_fd
        return child_fd


def promote_files(
    files: Iterable[Path],
    staging: Path,
    destination_dir: Path,
) -> None:
    staging_root = staging.resolve()
    destination_root = destination_dir.resolve()
    plan = _preflight_promotion(files, staging_root, destination_root)
    session = _PromotionSession(destination_root, staging_root)
    try:
        for src, rel_parts in plan:
            session.promote(src, rel_parts)
    except Exception as exc:
        rollback_errors = session.rollback()
        if rollback_errors:
            log.exception(
                "Promotion rollback encountered %d cleanup error(s)",
                len(rollback_errors),
            )
        if isinstance(exc, ArchiveExtractionError):
            if rollback_errors:
                msg = "promotion failed and rollback encountered cleanup errors"
                raise ArchiveExtractionError(msg) from exc
            raise
        msg = f"promotion failed: {exc}"
        raise ArchiveExtractionError(msg) from exc
    finally:
        session.close()


def _preflight_promotion(
    files: Iterable[Path],
    staging_root: Path,
    destination_root: Path,
) -> list[tuple[Path, tuple[str, ...]]]:
    plan: list[tuple[Path, tuple[str, ...]]] = []
    seen: set[tuple[str, ...]] = set()
    for src in files:
        rel_parts = src.relative_to(staging_root).parts
        dst = destination_root.joinpath(*rel_parts)
        _assert_contained(dst, destination_root)
        if rel_parts in seen:
            msg = f"duplicate promotion destination: {dst}"
            raise ArchiveExtractionError(msg)
        seen.add(rel_parts)
        plan.append((src, rel_parts))
    return plan


def _atomic_link_at(
    src_parent_fd: int,
    src_name: str,
    dst_parent_fd: int,
    dst_name: str,
) -> _PromotedArtifact:
    try:
        os.link(
            src_name,
            dst_name,
            src_dir_fd=src_parent_fd,
            dst_dir_fd=dst_parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        msg = f"destination file already exists: {dst_name!r}"
        raise ArchiveExtractionError(msg) from exc
    except OSError as exc:
        msg = f"promotion link failed: {exc}"
        raise ArchiveExtractionError(msg) from exc
    try:
        published = os.stat(dst_name, dir_fd=dst_parent_fd, follow_symlinks=False)
    except OSError as exc:
        try:
            os.unlink(dst_name, dir_fd=dst_parent_fd)
        except OSError:
            log.warning("Failed to remove destination after identity capture failure")
        msg = f"failed to capture promoted identity: {exc}"
        raise ArchiveExtractionError(msg) from exc
    if not stat.S_ISREG(published.st_mode):
        try:
            os.unlink(dst_name, dir_fd=dst_parent_fd)
        except OSError:
            log.warning("Failed to remove non-regular promoted file %s", dst_name)
        msg = f"promoted destination is not a regular file: {dst_name!r}"
        raise ArchiveExtractionError(msg)
    artifact = _PromotedArtifact(
        dst_parent_fd,
        dst_name,
        published.st_ino,
        published.st_dev,
    )
    try:
        os.unlink(src_name, dir_fd=src_parent_fd)
    except OSError as exc:
        _unlink_at_if_owned(artifact)
        msg = f"failed to remove staged source after promotion: {exc}"
        raise ArchiveExtractionError(msg) from exc
    return artifact


def _unlink_at_if_owned(artifact: _PromotedArtifact) -> None:
    try:
        current = os.stat(
            artifact.name,
            dir_fd=artifact.parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        return
    if stat.S_ISLNK(current.st_mode):
        return
    if current.st_ino == artifact.st_ino and current.st_dev == artifact.st_dev:
        os.unlink(artifact.name, dir_fd=artifact.parent_fd)

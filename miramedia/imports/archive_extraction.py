"""Safe archive extraction into a validated staging directory.

Untrusted archives are never extracted directly into import directories.
Extraction happens in a restrictive staging area; entries are validated for
containment, regular-file policy, and resource limits before promotion.

Format policy
-------------

Retained (stdlib extractors with preflight metadata + bounded streaming writes):

- zip, tar, tar.gz, tar.bz2, gzip, bzip2

Unsupported (fail closed — external extractors cannot prove limits before I/O):

- rar, 7z, freearc
"""

from __future__ import annotations

import bz2
import gzip
import logging
import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple, Protocol

log = logging.getLogger(__name__)

MAX_ARCHIVE_ENTRIES = 10_000
MAX_EXPANDED_BYTES = 50 * 1024**3
STAGING_DIR_MODE = 0o700
_COPY_CHUNK_SIZE = 64 * 1024

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:", re.ASCII)
_FORBIDDEN_PERCENT_SEQUENCES = ("%2f", "%5c", "%2e%2e", "%00")
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    },
)

RETAINED_ARCHIVE_FORMATS = frozenset(
    {"zip", "tar", "tar.gz", "tar.bz2", "gzip", "bzip2"},
)
UNSUPPORTED_ARCHIVE_FORMATS = frozenset({"rar", "7z", "freearc"})

_ARCHIVE_MIME_TYPES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-compressed",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/x-freearc",
        "application/x-bzip",
        "application/x-bzip2",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
    }
)


class ArchiveExtractionError(Exception):
    """Raised when an archive cannot be extracted safely."""


class _PromotedIdentity(NamedTuple):
    path: Path
    st_ino: int
    st_dev: int


class _ExpandedByteBudget:
    def __init__(self, limit: int = MAX_EXPANDED_BYTES) -> None:
        self._limit = limit
        self.total = 0

    def consume(self, nbytes: int) -> None:
        self.total += nbytes
        if self.total > self._limit:
            msg = f"archive exceeds expanded-byte limit ({self._limit})"
            raise ArchiveExtractionError(msg)


def is_archive_mime(mime: str | None) -> bool:
    return mime in _ARCHIVE_MIME_TYPES


def extract_archive_to_directory(archive: Path, destination_dir: Path) -> None:
    """Extract ``archive`` into ``destination_dir`` after staging validation."""
    archive = archive.resolve()
    destination_dir = destination_dir.resolve()
    if not archive.is_file():
        msg = f"archive does not exist: {archive}"
        raise ArchiveExtractionError(msg)
    if not destination_dir.is_dir():
        msg = f"destination is not a directory: {destination_dir}"
        raise ArchiveExtractionError(msg)

    staging = _create_staging_dir(destination_dir.parent)
    primary_error: BaseException | None = None
    try:
        archive_format = _detect_format(archive)
        _extract_to_staging(archive, staging, archive_format)
        files = _collect_validated_regular_files(staging)
        _promote_files(files, staging, destination_dir)
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        _cleanup_staging(staging, primary_error=primary_error)


def _create_staging_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".mm-extract-", dir=str(parent)),
    )
    staging.chmod(STAGING_DIR_MODE)
    return staging


def _cleanup_staging(
    staging: Path,
    *,
    primary_error: BaseException | None = None,
) -> None:
    if not staging.exists():
        return
    try:
        shutil.rmtree(staging)
    except OSError as cleanup_error:
        log.exception(
            "Failed to remove archive staging directory %s",
            staging,
        )
        if primary_error is not None:
            return
        msg = f"failed to clean staging directory: {staging}"
        raise ArchiveExtractionError(msg) from cleanup_error


def _detect_format(archive: Path) -> str:
    name = archive.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if name.endswith((".tar.bz2", ".tbz2")):
        return "tar.bz2"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".rar"):
        return "rar"
    if name.endswith(".7z"):
        return "7z"
    if name.endswith(".arc"):
        return "freearc"
    if name.endswith(".gz") and not name.endswith(".tar.gz"):
        return "gzip"
    if name.endswith(".bz2") and not name.endswith(".tar.bz2"):
        return "bzip2"

    mime = _guess_mime(archive)
    if mime in {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-compressed",
    }:
        return "zip"
    if mime == "application/vnd.rar":
        return "rar"
    if mime == "application/x-7z-compressed":
        return "7z"
    if mime == "application/x-freearc":
        return "freearc"
    if mime in {"application/x-bzip", "application/x-bzip2"}:
        return "bzip2"
    if mime in {"application/gzip", "application/x-gzip"}:
        return "gzip"
    if mime == "application/x-tar":
        return "tar"

    msg = f"unsupported archive format: {archive.name}"
    raise ArchiveExtractionError(msg)


def _guess_mime(archive: Path) -> str | None:
    import mimetypes

    return mimetypes.guess_type(archive)[0]


def _unsupported_format_error(archive_format: str) -> ArchiveExtractionError:
    msg = (
        f"archive format not supported for safe extraction: {archive_format} "
        f"(retained formats: {', '.join(sorted(RETAINED_ARCHIVE_FORMATS))})"
    )
    return ArchiveExtractionError(msg)


def _extract_to_staging(archive: Path, staging: Path, archive_format: str) -> None:
    if archive_format in UNSUPPORTED_ARCHIVE_FORMATS:
        raise _unsupported_format_error(archive_format)
    if archive_format in RETAINED_ARCHIVE_FORMATS:
        _extract_with_stdlib(archive, staging, archive_format)
        return
    msg = f"unsupported archive format: {archive_format}"
    raise ArchiveExtractionError(msg)


def _extract_with_stdlib(archive: Path, staging: Path, archive_format: str) -> None:
    if archive_format == "zip":
        _extract_zip(archive, staging)
    elif archive_format == "tar":
        _extract_tar(archive, staging, mode="r:")
    elif archive_format == "tar.gz":
        _extract_tar(archive, staging, mode="r:gz")
    elif archive_format == "tar.bz2":
        _extract_tar(archive, staging, mode="r:bz2")
    elif archive_format == "gzip":
        _extract_gzip(archive, staging)
    elif archive_format == "bzip2":
        _extract_bzip2(archive, staging)
    else:
        msg = f"unsupported stdlib format: {archive_format}"
        raise ArchiveExtractionError(msg)


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _Writable(Protocol):
    def write(self, data: bytes, /) -> int: ...


def _copy_stream_bounded(
    src: _Readable,
    dst: _Writable,
    *,
    budget: _ExpandedByteBudget,
) -> None:
    while True:
        chunk = src.read(_COPY_CHUNK_SIZE)
        if not chunk:
            return
        budget.consume(len(chunk))
        dst.write(chunk)


def _extract_zip(archive: Path, staging: Path) -> None:
    budget = _ExpandedByteBudget()
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        _validate_zip_metadata(infos)
        for info in infos:
            if info.is_dir():
                continue
            rel = _safe_relative_path(staging, info.filename)
            rel.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, rel.open("wb") as dst:
                _copy_stream_bounded(src, dst, budget=budget)


def _extract_tar(
    archive: Path,
    staging: Path,
    *,
    mode: Literal["r:", "r:gz", "r:bz2"],
) -> None:
    budget = _ExpandedByteBudget()
    with tarfile.open(archive, mode) as tf:
        members = tf.getmembers()
        _validate_tar_metadata(members)
        extract_filter = getattr(tarfile, "data_filter", None)
        for member in members:
            if member.isdir():
                _safe_relative_path(staging, member.name).mkdir(
                    parents=True, exist_ok=True
                )
                continue
            if extract_filter is not None:
                safe = extract_filter(member, staging)
            else:
                safe = member
            rel = _safe_relative_path(staging, safe.name)
            rel.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(safe)
            if src is None:
                msg = f"failed to read tar member: {safe.name}"
                raise ArchiveExtractionError(msg)
            with src, rel.open("wb") as dst:
                _copy_stream_bounded(src, dst, budget=budget)


def _extract_gzip(archive: Path, staging: Path) -> None:
    out_name = (
        archive.name[:-3]
        if archive.name.lower().endswith(".gz")
        else f"{archive.name}.out"
    )
    _validate_entry_name(out_name)
    _enforce_entry_count(1)
    rel = _safe_relative_path(staging, out_name)
    rel.parent.mkdir(parents=True, exist_ok=True)
    budget = _ExpandedByteBudget()
    with gzip.open(archive, "rb") as src, rel.open("wb") as dst:
        _copy_stream_bounded(src, dst, budget=budget)


def _extract_bzip2(archive: Path, staging: Path) -> None:
    out_name = (
        archive.name[:-4]
        if archive.name.lower().endswith(".bz2")
        else f"{archive.name}.out"
    )
    _validate_entry_name(out_name)
    _enforce_entry_count(1)
    rel = _safe_relative_path(staging, out_name)
    rel.parent.mkdir(parents=True, exist_ok=True)
    budget = _ExpandedByteBudget()
    with bz2.open(archive, "rb") as src, rel.open("wb") as dst:
        _copy_stream_bounded(src, dst, budget=budget)


def _validate_zip_metadata(infos: Iterable[zipfile.ZipInfo]) -> None:
    file_count = 0
    total_bytes = 0
    for info in infos:
        if info.is_dir():
            continue
        _validate_entry_name(info.filename)
        if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
            msg = f"archive contains symlink entry: {info.filename}"
            raise ArchiveExtractionError(msg)
        file_count += 1
        total_bytes += info.file_size
        _enforce_limits(file_count, total_bytes)


def _validate_tar_metadata(members: Iterable[tarfile.TarInfo]) -> None:
    file_count = 0
    total_bytes = 0
    for member in members:
        _validate_entry_name(member.name)
        if member.issym() or member.islnk():
            msg = f"archive contains link entry: {member.name}"
            raise ArchiveExtractionError(msg)
        if member.isdev() or member.isfifo():
            msg = f"archive contains non-regular entry: {member.name}"
            raise ArchiveExtractionError(msg)
        if member.isfile():
            file_count += 1
            total_bytes += member.size
            _enforce_limits(file_count, total_bytes)


def _validate_entry_name(name: str) -> None:
    if not name or name in {".", ".."}:
        msg = f"unsafe archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    if "\0" in name:
        msg = f"archive entry name contains NUL: {name!r}"
        raise ArchiveExtractionError(msg)
    if name.startswith(("/", "\\")):
        msg = f"absolute archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    if name.startswith(("//", "\\\\")):
        msg = f"unc archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    if _WINDOWS_DRIVE_RE.match(name):
        msg = f"drive-anchored archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    if "\\" in name:
        msg = f"mixed-separator archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    lowered = name.lower()
    for sequence in _FORBIDDEN_PERCENT_SEQUENCES:
        if sequence in lowered:
            msg = f"encoded archive entry name is not allowed: {name!r}"
            raise ArchiveExtractionError(msg)
    parts = PurePosixPath(name).parts
    if ".." in parts:
        msg = f"traversal archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    for part in parts:
        if _is_reserved_windows_name(part):
            msg = f"reserved windows archive entry name: {name!r}"
            raise ArchiveExtractionError(msg)


def _is_reserved_windows_name(part: str) -> bool:
    stem = part.split(".", 1)[0].upper()
    return stem in _RESERVED_WINDOWS_NAMES


def _enforce_entry_count(file_count: int) -> None:
    if file_count > MAX_ARCHIVE_ENTRIES:
        msg = f"archive exceeds entry limit ({MAX_ARCHIVE_ENTRIES})"
        raise ArchiveExtractionError(msg)


def _enforce_limits(file_count: int, total_bytes: int) -> None:
    _enforce_entry_count(file_count)
    if total_bytes > MAX_EXPANDED_BYTES:
        msg = f"archive exceeds expanded-byte limit ({MAX_EXPANDED_BYTES})"
        raise ArchiveExtractionError(msg)


def _safe_relative_path(root: Path, entry_name: str) -> Path:
    _validate_entry_name(entry_name)
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*PurePosixPath(entry_name).parts)
    _assert_contained(candidate, root_resolved)
    return candidate


def _assert_contained(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        msg = f"path escapes staging root: {path}"
        raise ArchiveExtractionError(msg) from exc


def _assert_no_symlink_components(path: Path, root: Path) -> None:
    current = path
    while current != root:
        if current.is_symlink():
            msg = f"symlink in destination path: {current}"
            raise ArchiveExtractionError(msg)
        parent = current.parent
        if parent == current:
            break
        current = parent


def _collect_validated_regular_files(staging: Path) -> list[Path]:
    staging_root = staging.resolve()
    files: list[Path] = []
    file_count = 0
    total_bytes = 0
    for path in sorted(staging_root.rglob("*")):
        try:
            lstat = path.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(lstat.st_mode):
            msg = f"extracted symlink is not allowed: {path}"
            raise ArchiveExtractionError(msg)
        if stat.S_ISDIR(lstat.st_mode):
            continue
        if not stat.S_ISREG(lstat.st_mode):
            msg = f"extracted non-regular file is not allowed: {path}"
            raise ArchiveExtractionError(msg)
        if lstat.st_nlink > 1:
            msg = f"extracted hardlink is not allowed: {path}"
            raise ArchiveExtractionError(msg)
        contained = _assert_contained_return(path, staging_root)
        file_count += 1
        total_bytes += lstat.st_size
        _enforce_limits(file_count, total_bytes)
        files.append(contained)
    return files


def _assert_contained_return(path: Path, root: Path) -> Path:
    _assert_contained(path, root)
    return path


def _preflight_promotion(
    files: Iterable[Path],
    staging: Path,
    destination_dir: Path,
) -> list[tuple[Path, Path]]:
    staging_root = staging.resolve()
    destination_root = destination_dir.resolve()
    plan: list[tuple[Path, Path]] = []
    seen_destinations: set[Path] = set()

    for src in files:
        rel = src.relative_to(staging_root)
        dst = destination_root / rel
        _assert_contained(dst, destination_root)
        _assert_no_symlink_components(dst.parent, destination_root)
        if dst in seen_destinations:
            msg = f"duplicate promotion destination: {dst}"
            raise ArchiveExtractionError(msg)
        seen_destinations.add(dst)
        if dst.exists():
            msg = f"destination file already exists: {dst}"
            raise ArchiveExtractionError(msg)
        parent = dst.parent
        if parent.exists():
            if parent.is_symlink() or not parent.is_dir():
                msg = f"promotion parent is not a directory: {parent}"
                raise ArchiveExtractionError(msg)
        plan.append((src, dst))

    return plan


def _promote_files(files: Iterable[Path], staging: Path, destination_dir: Path) -> None:
    destination_root = destination_dir.resolve()
    plan = _preflight_promotion(files, staging, destination_dir)
    promoted_files: list[_PromotedIdentity] = []
    created_dirs: list[Path] = []

    try:
        for src, dst in plan:
            created_dirs.extend(_create_parent_dirs(dst.parent, destination_root))
            promoted_files.append(_atomic_promote_file(src, dst))
    except (ArchiveExtractionError, OSError) as exc:
        _rollback_promotion(promoted_files, created_dirs, destination_root)
        if isinstance(exc, ArchiveExtractionError):
            raise
        msg = f"promotion failed: {exc}"
        raise ArchiveExtractionError(msg) from exc


def _atomic_promote_file(src: Path, dst: Path) -> _PromotedIdentity:
    try:
        os.link(src, dst)
    except FileExistsError as exc:
        msg = f"destination file already exists: {dst}"
        raise ArchiveExtractionError(msg) from exc
    try:
        identity = _promoted_identity(dst)
    except OSError:
        _unlink_fresh_link(dst)
        raise
    try:
        src.unlink()
    except OSError:
        _unlink_if_owned(identity)
        raise
    return identity


def _promoted_identity(path: Path) -> _PromotedIdentity:
    stat_result = path.lstat()
    return _PromotedIdentity(path, stat_result.st_ino, stat_result.st_dev)


def _unlink_fresh_link(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        log.warning("Failed to remove freshly linked destination: %s", path)


def _unlink_if_owned(identity: _PromotedIdentity) -> None:
    try:
        current = identity.path.lstat()
    except OSError:
        return
    if stat.S_ISLNK(current.st_mode):
        return
    if current.st_ino == identity.st_ino and current.st_dev == identity.st_dev:
        identity.path.unlink()


def _create_parent_dirs(leaf: Path, destination_root: Path) -> list[Path]:
    created: list[Path] = []
    for directory in _missing_parent_dirs(leaf, destination_root):
        _assert_no_symlink_components(directory, destination_root)
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                msg = f"promotion parent is not a directory: {directory}"
                raise ArchiveExtractionError(msg)
            continue
        directory.mkdir()
        created.append(directory)
    return created


def _missing_parent_dirs(start: Path, stop: Path) -> Iterator[Path]:
    needed: list[Path] = []
    current = start
    while current != stop and not current.exists():
        needed.append(current)
        if current.parent == current:
            break
        current = current.parent
    yield from reversed(needed)


def _rollback_promotion(
    promoted_files: list[_PromotedIdentity],
    created_dirs: list[Path],
    destination_root: Path,
) -> None:
    for identity in reversed(promoted_files):
        _unlink_if_owned(identity)
    for directory in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
        if (
            directory.exists()
            and directory.is_dir()
            and directory != destination_root
            and not any(directory.iterdir())
        ):
            try:
                directory.rmdir()
            except OSError:
                log.warning(
                    "Failed to remove promotion directory during rollback: %s",
                    directory,
                )

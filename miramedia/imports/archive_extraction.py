"""Safe archive extraction into a validated staging directory.

Untrusted archives are never extracted directly into import directories.
Extraction happens in a restrictive staging area; entries are validated for
containment, regular-file policy, and resource limits before promotion.

Format policy
-------------

Retained (bounded stdlib parsers only):

- zip, tar, tar.gz, tar.bz2, gzip, bzip2

Unsupported (fail closed):

- rar, 7z, freearc and common aliases
- tar.xz (no bounded xz tar parser; explicit migration decision)
- zip64 (not safely preflighted)
"""

from __future__ import annotations

import logging
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

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
UNSUPPORTED_ARCHIVE_FORMATS = frozenset(
    {"rar", "7z", "freearc", "tar.xz", "zip64"},
)

_RAR_EXTENSIONS = (".rar", ".cbr", ".rev")
_7Z_EXTENSIONS = (".7z", ".cb7")
_FREARC_EXTENSIONS = (".arc",)
_TAR_XZ_EXTENSIONS = (".tar.xz", ".txz")

_ARCHIVE_MIME_TYPES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-compressed",
        "application/vnd.rar",
        "application/x-rar-compressed",
        "application/x-7z-compressed",
        "application/x-freearc",
        "application/x-bzip",
        "application/x-bzip2",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
        "application/x-xz",
    }
)


class ArchiveExtractionError(Exception):
    """Raised when an archive cannot be extracted safely."""


class _ExpandedByteBudget:
    def __init__(self, limit: int | None = None) -> None:
        self._limit = MAX_EXPANDED_BYTES if limit is None else limit
        self.total = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def remaining(self) -> int:
        return self._limit - self.total

    def consume(self, nbytes: int) -> None:
        self.total += nbytes
        if self.total > self._limit:
            msg = f"archive exceeds expanded-byte limit ({self._limit})"
            raise ArchiveExtractionError(msg)


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _Writable(Protocol):
    def write(self, data: bytes, /) -> int: ...


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
        from miramedia.imports.archive_promotion import promote_files

        promote_files(files, staging, destination_dir)
    except Exception as exc:
        primary_error = exc
        if not isinstance(exc, ArchiveExtractionError):
            msg = f"archive extraction failed: {exc}"
            raise ArchiveExtractionError(msg) from exc
        raise
    finally:
        _cleanup_staging(staging, primary_error=primary_error)


def _create_staging_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mm-extract-", dir=str(parent)))
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
    if name.endswith(_TAR_XZ_EXTENSIONS):
        return "tar.xz"
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if name.endswith((".tar.bz2", ".tbz2")):
        return "tar.bz2"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(_RAR_EXTENSIONS):
        return "rar"
    if name.endswith(_7Z_EXTENSIONS):
        return "7z"
    if name.endswith(_FREARC_EXTENSIONS):
        return "freearc"
    if name.endswith(".gz"):
        return "gzip"
    if name.endswith(".bz2"):
        return "bzip2"

    mime, encoding = _guess_mime_encoding(archive)
    if encoding in {"gzip", "x-gzip"} and not name.endswith(".tar.gz"):
        return "gzip"
    if encoding in {"bzip2", "x-bzip2"} and not name.endswith(".tar.bz2"):
        return "bzip2"
    if encoding == "xz":
        return "tar.xz"
    if mime in {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-compressed",
    }:
        return "zip"
    if mime in {"application/vnd.rar", "application/x-rar-compressed"}:
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
    if mime == "application/x-xz":
        return "tar.xz"

    msg = f"unsupported archive format: {archive.name}"
    raise ArchiveExtractionError(msg)


def _guess_mime_encoding(archive: Path) -> tuple[str | None, str | None]:
    import mimetypes

    mime, encoding = mimetypes.guess_type(archive)
    return mime, encoding


def _unsupported_format_error(archive_format: str) -> ArchiveExtractionError:
    msg = (
        f"archive format not supported for safe extraction: {archive_format} "
        f"(retained formats: {', '.join(sorted(RETAINED_ARCHIVE_FORMATS))})"
    )
    return ArchiveExtractionError(msg)


def _extract_to_staging(archive: Path, staging: Path, archive_format: str) -> None:
    if archive_format in UNSUPPORTED_ARCHIVE_FORMATS:
        raise _unsupported_format_error(archive_format)
    if archive_format not in RETAINED_ARCHIVE_FORMATS:
        msg = f"unsupported archive format: {archive_format}"
        raise ArchiveExtractionError(msg)
    from miramedia.imports import archive_parsers as parsers

    budget = _ExpandedByteBudget()
    if archive_format == "zip":
        parsers.extract_zip_archive(archive, staging, budget)
    elif archive_format == "tar":
        parsers.extract_tar_archive(archive, staging, budget, compression=None)
    elif archive_format == "tar.gz":
        parsers.extract_tar_archive(archive, staging, budget, compression="gz")
    elif archive_format == "tar.bz2":
        parsers.extract_tar_archive(archive, staging, budget, compression="bz2")
    elif archive_format == "gzip":
        parsers.extract_gzip_archive(
            archive,
            staging,
            budget,
            out_name=_gzip_output_name(archive),
        )
    elif archive_format == "bzip2":
        parsers.extract_bzip2_archive(
            archive,
            staging,
            budget,
            out_name=_bzip2_output_name(archive),
        )
    else:
        msg = f"unsupported archive format: {archive_format}"
        raise ArchiveExtractionError(msg)


def _gzip_output_name(archive: Path) -> str:
    if archive.name.lower().endswith(".gz"):
        return archive.name[:-3]
    return f"{archive.name}.out"


def _bzip2_output_name(archive: Path) -> str:
    if archive.name.lower().endswith(".bz2"):
        return archive.name[:-4]
    return f"{archive.name}.out"


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


def _collect_validated_regular_files(staging: Path) -> list[Path]:
    staging_root = staging.resolve()
    files: list[Path] = []
    file_count = 0
    total_bytes = 0
    for path in sorted(staging_root.rglob("*")):
        try:
            lstat = path.lstat()
        except OSError as exc:
            msg = f"failed to inspect extracted entry: {path}"
            raise ArchiveExtractionError(msg) from exc
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
        _assert_contained(path, staging_root)
        file_count += 1
        total_bytes += lstat.st_size
        _enforce_limits(file_count, total_bytes)
        files.append(path)
    return files

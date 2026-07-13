"""Safe archive extraction into a validated staging directory.

Untrusted archives are never extracted directly into import directories.
Extraction happens in a restrictive staging area; entries are validated for
containment, regular-file policy, and resource limits before promotion.
"""

from __future__ import annotations

import bz2
import gzip
import logging
import os
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal

log = logging.getLogger(__name__)

MAX_ARCHIVE_ENTRIES = 10_000
MAX_EXPANDED_BYTES = 50 * 1024**3
EXTRACTION_TIMEOUT_SECONDS = 600
STAGING_DIR_MODE = 0o700

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

_STDLIB_FORMATS = frozenset(
    {
        "zip",
        "tar",
        "tar.gz",
        "tar.bz2",
        "gzip",
        "bzip2",
    }
)
_PATOOL_FORMATS = frozenset({"rar", "7z", "freearc"})


class ArchiveExtractionError(Exception):
    """Raised when an archive cannot be extracted safely."""


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
    try:
        archive_format = _detect_format(archive)
        _extract_to_staging(archive, staging, archive_format)
        files = _collect_validated_regular_files(staging)
        _promote_files(files, staging, destination_dir)
    finally:
        _cleanup_staging(staging)


def _create_staging_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".mm-extract-", dir=str(parent)),
    )
    staging.chmod(STAGING_DIR_MODE)
    return staging


def _cleanup_staging(staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


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
    if mime in {"application/zip", "application/x-zip-compressed", "application/x-compressed"}:
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


def _extract_to_staging(archive: Path, staging: Path, archive_format: str) -> None:
    if archive_format in _STDLIB_FORMATS:
        _extract_with_stdlib(archive, staging, archive_format)
        return
    if archive_format in _PATOOL_FORMATS:
        _extract_with_patool_subprocess(archive, staging)
        return
    msg = f"no safe extractor for format {archive_format}"
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


def _extract_zip(archive: Path, staging: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        _validate_zip_metadata(infos)
        for info in infos:
            if info.is_dir():
                continue
            rel = _safe_relative_path(staging, info.filename)
            rel.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, rel.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _extract_tar(
    archive: Path,
    staging: Path,
    *,
    mode: Literal["r:", "r:gz", "r:bz2"],
) -> None:
    with tarfile.open(archive, mode) as tf:
        members = tf.getmembers()
        _validate_tar_metadata(members)
        extract_filter = getattr(tarfile, "data_filter", None)
        for member in members:
            if member.isdir():
                _safe_relative_path(staging, member.name).mkdir(parents=True, exist_ok=True)
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
                shutil.copyfileobj(src, dst)


def _extract_gzip(archive: Path, staging: Path) -> None:
    if archive.stat().st_size > MAX_EXPANDED_BYTES:
        msg = "archive exceeds expanded-byte limit"
        raise ArchiveExtractionError(msg)
    out_name = archive.name[:-3] if archive.name.lower().endswith(".gz") else f"{archive.name}.out"
    _validate_entry_name(out_name)
    _enforce_limits(1, archive.stat().st_size)
    rel = _safe_relative_path(staging, out_name)
    rel.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "rb") as src, rel.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def _extract_bzip2(archive: Path, staging: Path) -> None:
    if archive.stat().st_size > MAX_EXPANDED_BYTES:
        msg = "archive exceeds expanded-byte limit"
        raise ArchiveExtractionError(msg)
    out_name = archive.name[:-4] if archive.name.lower().endswith(".bz2") else f"{archive.name}.out"
    _validate_entry_name(out_name)
    _enforce_limits(1, archive.stat().st_size)
    rel = _safe_relative_path(staging, out_name)
    rel.parent.mkdir(parents=True, exist_ok=True)
    with bz2.open(archive, "rb") as src, rel.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def _extract_with_patool_subprocess(archive: Path, staging: Path) -> None:
    script = (
        "import patoolib\n"
        f"patoolib.extract_archive({str(archive)!r}, outdir={str(staging)!r}, "
        "interactive=False, verbosity=-1)\n"
    )
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=EXTRACTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(proc)
        msg = "archive extraction timed out"
        raise ArchiveExtractionError(msg) from exc
    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace").strip()
        msg = f"archive extraction failed: {detail or 'unknown error'}"
        raise ArchiveExtractionError(msg)


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        proc.terminate()
    proc.wait(timeout=5)


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
    if name.startswith(("/", "\\")):
        msg = f"absolute archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    if "\\" in name:
        msg = f"mixed-separator archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)
    parts = PurePosixPath(name).parts
    if ".." in parts:
        msg = f"traversal archive entry name: {name!r}"
        raise ArchiveExtractionError(msg)


def _enforce_limits(file_count: int, total_bytes: int) -> None:
    if file_count > MAX_ARCHIVE_ENTRIES:
        msg = f"archive exceeds entry limit ({MAX_ARCHIVE_ENTRIES})"
        raise ArchiveExtractionError(msg)
    if total_bytes > MAX_EXPANDED_BYTES:
        msg = f"archive exceeds expanded-byte limit ({MAX_EXPANDED_BYTES})"
        raise ArchiveExtractionError(msg)


def _safe_relative_path(root: Path, entry_name: str) -> Path:
    _validate_entry_name(entry_name)
    rel = (root / entry_name).resolve()
    _assert_contained(rel, root.resolve())
    return rel


def _assert_contained(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        msg = f"path escapes staging root: {path}"
        raise ArchiveExtractionError(msg)


def _collect_validated_regular_files(staging: Path) -> list[Path]:
    staging_root = staging.resolve()
    files: list[Path] = []
    file_count = 0
    total_bytes = 0
    for path in sorted(staging_root.rglob("*")):
        if not path.exists():
            continue
        lstat = path.lstat()
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
        resolved = _assert_contained_return(path, staging_root)
        file_count += 1
        total_bytes += lstat.st_size
        _enforce_limits(file_count, total_bytes)
        files.append(resolved)
    return files


def _assert_contained_return(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    _assert_contained(resolved, root)
    return resolved


def _promote_files(files: Iterable[Path], staging: Path, destination_dir: Path) -> None:
    staging_root = staging.resolve()
    destination_root = destination_dir.resolve()
    for src in files:
        rel = src.relative_to(staging_root)
        dst = (destination_root / rel).resolve()
        _assert_contained(dst, destination_root)
        if dst.exists():
            msg = f"destination file already exists: {dst}"
            raise ArchiveExtractionError(msg)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)

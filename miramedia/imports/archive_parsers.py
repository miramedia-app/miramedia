"""Bounded archive parsers with sequential resource enforcement."""

from __future__ import annotations

import bz2
import gzip
import lzma
import stat as stat_mod
import struct
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import IO, cast

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    _copy_stream_bounded,
    _enforce_entry_count,
    _enforce_limits,
    _ExpandedByteBudget,
    _safe_relative_path,
    _validate_entry_name,
)

_TAR_BLOCK = 512
_MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 64 * 1024 * 1024
_EOCD_STRUCT = struct.Struct("<4sHHHHIIH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_APPROVED_TAR_TYPES = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        b"",
    },
)


def preflight_zip_archive(archive: Path) -> None:
    size = archive.stat().st_size
    if size < _EOCD_STRUCT.size:
        msg = "malformed zip archive"
        raise ArchiveExtractionError(msg)
    with archive.open("rb") as handle:
        handle.seek(max(0, size - 65557))
        tail = handle.read()
    if _ZIP64_LOCATOR_SIGNATURE in tail:
        msg = "zip64 archives are not supported"
        raise ArchiveExtractionError(msg)
    offset = tail.rfind(_EOCD_SIGNATURE)
    if offset < 0 or len(tail) - offset < _EOCD_STRUCT.size:
        msg = "malformed zip archive"
        raise ArchiveExtractionError(msg)
    (
        _signature,
        _disk_no,
        _disk_with_cd,
        entries_on_disk,
        entry_count,
        central_directory_size,
        _cd_offset,
        comment_length,
    ) = _EOCD_STRUCT.unpack(tail[offset : offset + _EOCD_STRUCT.size])
    if entries_on_disk == 0xFFFF or entry_count == 0xFFFF:
        msg = "zip64 archives are not supported"
        raise ArchiveExtractionError(msg)
    if central_directory_size == 0xFFFFFFFF:
        msg = "zip64 archives are not supported"
        raise ArchiveExtractionError(msg)
    _enforce_entry_count(max(entry_count, entries_on_disk))
    if central_directory_size > _MAX_ZIP_CENTRAL_DIRECTORY_BYTES:
        msg = "zip central directory exceeds size limit"
        raise ArchiveExtractionError(msg)
    if comment_length and offset + _EOCD_STRUCT.size + comment_length > len(tail):
        msg = "malformed zip archive"
        raise ArchiveExtractionError(msg)


def extract_zip_archive(
    archive: Path,
    staging: Path,
    budget: _ExpandedByteBudget,
) -> None:
    preflight_zip_archive(archive)
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = list(zf.infolist())
            _validate_zip_entries(infos)
            for info in infos:
                if info.is_dir():
                    _safe_relative_path(staging, info.filename).mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    continue
                rel = _safe_relative_path(staging, info.filename)
                rel.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, rel.open("wb") as dst:
                    _copy_stream_bounded(src, dst, budget=budget)
    except zipfile.BadZipFile as exc:
        msg = "malformed zip archive"
        raise ArchiveExtractionError(msg) from exc
    except OSError as exc:
        msg = f"zip extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc
    except RuntimeError as exc:
        if "encrypted" in str(exc).lower():
            msg = "encrypted zip archives are not supported"
            raise ArchiveExtractionError(msg) from exc
        raise


def _validate_zip_entries(infos: Iterable[zipfile.ZipInfo]) -> None:
    entry_count = 0
    total_bytes = 0
    for info in infos:
        entry_count += 1
        _validate_entry_name(info.filename)
        if info.is_dir():
            _enforce_entry_count(entry_count)
            continue
        if stat_mod.S_IFMT(info.external_attr >> 16) == stat_mod.S_IFLNK:
            msg = f"archive contains symlink entry: {info.filename}"
            raise ArchiveExtractionError(msg)
        total_bytes += info.file_size
        _enforce_limits(entry_count, total_bytes)


def extract_tar_archive(
    archive: Path,
    staging: Path,
    budget: _ExpandedByteBudget,
    *,
    compression: str | None,
) -> None:
    try:
        with _open_tar_stream(archive, compression) as stream:
            _parse_tar_stream(stream, staging, budget)
    except ArchiveExtractionError:
        raise
    except (tarfile.TarError, OSError, lzma.LZMAError, EOFError) as exc:
        msg = f"tar extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc


def _open_tar_stream(archive: Path, compression: str | None) -> IO[bytes]:
    raw = archive.open("rb")
    if compression is None:
        return raw
    if compression == "gz":
        return cast(IO[bytes], gzip.GzipFile(fileobj=raw))
    if compression == "bz2":
        return cast(IO[bytes], bz2.BZ2File(raw))
    if compression == "xz":
        return cast(IO[bytes], lzma.LZMAFile(raw))
    msg = f"unsupported tar compression: {compression}"
    raise ArchiveExtractionError(msg)


def _parse_tar_stream(
    stream: IO[bytes],
    staging: Path,
    budget: _ExpandedByteBudget,
) -> None:
    entry_count = 0
    while True:
        header = _read_exact(stream, _TAR_BLOCK)
        if header == b"\0" * _TAR_BLOCK:
            return
        typeflag = header[156:157]
        if typeflag in {tarfile.XHDTYPE, tarfile.XGLTYPE}:
            msg = "tar pax extended headers are not allowed"
            raise ArchiveExtractionError(msg)
        if typeflag not in _APPROVED_TAR_TYPES:
            msg = f"unsupported tar entry type: {typeflag!r}"
            raise ArchiveExtractionError(msg)
        try:
            member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        except tarfile.TarError as exc:
            msg = "malformed tar header"
            raise ArchiveExtractionError(msg) from exc
        entry_count += 1
        _enforce_entry_count(entry_count)
        _validate_entry_name(member.name)
        if member.isdir():
            _safe_relative_path(staging, member.name).mkdir(
                parents=True,
                exist_ok=True,
            )
            _skip_tar_payload(stream, member.size)
            continue
        if not member.isreg():
            msg = f"unsupported tar entry type: {member.type!r}"
            raise ArchiveExtractionError(msg)
        if member.size > budget.remaining:
            msg = f"archive exceeds expanded-byte limit ({budget.limit})"
            raise ArchiveExtractionError(msg)
        rel = _safe_relative_path(staging, member.name)
        rel.parent.mkdir(parents=True, exist_ok=True)
        with rel.open("wb") as dst:
            _copy_tar_payload(stream, dst, member.size, budget=budget)
        _skip_tar_padding(stream, member.size)


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    data = stream.read(size)
    if data is None or len(data) != size:
        msg = "unexpected end of tar archive"
        raise ArchiveExtractionError(msg)
    return data


def _copy_tar_payload(
    stream: IO[bytes],
    dst: IO[bytes],
    size: int,
    *,
    budget: _ExpandedByteBudget,
) -> None:
    remaining = size
    while remaining > 0:
        chunk = _read_exact(stream, min(64 * 1024, remaining))
        budget.consume(len(chunk))
        dst.write(chunk)
        remaining -= len(chunk)


def _skip_tar_payload(stream: IO[bytes], size: int) -> None:
    remaining = size
    while remaining > 0:
        skipped = stream.read(min(64 * 1024, remaining))
        if not skipped:
            msg = "unexpected end of tar archive"
            raise ArchiveExtractionError(msg)
        remaining -= len(skipped)
    _skip_tar_padding(stream, size)


def _skip_tar_padding(stream: IO[bytes], size: int) -> None:
    padding = (_TAR_BLOCK - (size % _TAR_BLOCK)) % _TAR_BLOCK
    if padding:
        _read_exact(stream, padding)


def extract_gzip_archive(
    archive: Path,
    staging: Path,
    budget: _ExpandedByteBudget,
    *,
    out_name: str,
) -> None:
    _validate_entry_name(out_name)
    _enforce_entry_count(1)
    rel = _safe_relative_path(staging, out_name)
    rel.parent.mkdir(parents=True, exist_ok=True)
    try:
        with gzip.open(archive, "rb") as src, rel.open("wb") as dst:
            _copy_stream_bounded(src, dst, budget=budget)
    except (OSError, EOFError) as exc:
        msg = f"gzip extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc


def extract_bzip2_archive(
    archive: Path,
    staging: Path,
    budget: _ExpandedByteBudget,
    *,
    out_name: str,
) -> None:
    _validate_entry_name(out_name)
    _enforce_entry_count(1)
    rel = _safe_relative_path(staging, out_name)
    rel.parent.mkdir(parents=True, exist_ok=True)
    try:
        with bz2.open(archive, "rb") as src, rel.open("wb") as dst:
            _copy_stream_bounded(src, dst, budget=budget)
    except (OSError, EOFError) as exc:
        msg = f"bzip2 extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc

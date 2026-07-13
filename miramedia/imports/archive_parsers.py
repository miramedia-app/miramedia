"""Bounded archive parsers with sequential resource enforcement."""

from __future__ import annotations

import bz2
import contextlib
import gzip
import os
import stat as stat_mod
import struct
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO, NamedTuple, cast

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    _enforce_entry_count,
    _enforce_limits,
    _EntryPathRegistry,
    _ExpandedByteBudget,
    _validate_entry_name,
)
from miramedia.imports.archive_staging_io import (
    mkdir_entry,
    open_entry_for_write,
    write_entry_stream,
)

_TAR_BLOCK = 512
_MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 64 * 1024 * 1024
_EOCD_STRUCT = struct.Struct("<4sHHHHIIH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_CD_HEADER_STRUCT = struct.Struct("<4sHHHHHHIIIHHHHHII")
_CD_HEADER_SIGNATURE = b"PK\x01\x02"
_ZIP_UTF8_FLAG = 0x800
_ZIP64_EXTRA_HEADER_ID = 0x0001
_APPROVED_TAR_TYPES = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        b"",
    },
)


class _ZipCentralDirectoryPreflight(NamedTuple):
    entry_count: int
    central_directory_offset: int
    central_directory_size: int


def preflight_zip_archive(archive: Path) -> _ZipCentralDirectoryPreflight:
    file_size = archive.stat().st_size
    if file_size < _EOCD_STRUCT.size:
        msg = "malformed zip archive"
        raise ArchiveExtractionError(msg)

    with archive.open("rb") as handle:
        handle.seek(max(0, file_size - 65557))
        tail = handle.read()
    if _ZIP64_LOCATOR_SIGNATURE in tail:
        msg = "zip64 archives are not supported"
        raise ArchiveExtractionError(msg)

    eocd_offset, fields = _locate_eocd_at_eof(archive, file_size)
    (
        _signature,
        disk_no,
        disk_with_cd,
        entries_on_disk,
        entry_count,
        central_directory_size,
        central_directory_offset,
        _comment_length,
    ) = fields

    if disk_no != 0 or disk_with_cd != 0:
        msg = "multi-disk zip archives are not supported"
        raise ArchiveExtractionError(msg)
    if entries_on_disk == 0xFFFF or entry_count == 0xFFFF:
        msg = "zip64 archives are not supported"
        raise ArchiveExtractionError(msg)
    if central_directory_size == 0xFFFFFFFF or central_directory_offset == 0xFFFFFFFF:
        msg = "zip64 archives are not supported"
        raise ArchiveExtractionError(msg)
    if entries_on_disk != entry_count:
        msg = "zip central directory entry counts disagree"
        raise ArchiveExtractionError(msg)
    if central_directory_size > _MAX_ZIP_CENTRAL_DIRECTORY_BYTES:
        msg = "zip central directory exceeds size limit"
        raise ArchiveExtractionError(msg)
    if central_directory_offset > file_size:
        msg = "zip central directory offset out of bounds"
        raise ArchiveExtractionError(msg)
    if central_directory_offset + central_directory_size != eocd_offset:
        msg = "zip central directory is not adjacent to end-of-central-directory"
        raise ArchiveExtractionError(msg)

    actual_count = _parse_zip_central_directory(
        archive,
        central_directory_offset,
        central_directory_size,
    )
    if actual_count != entry_count:
        msg = (
            "zip central directory record count disagrees with end-of-central-directory"
        )
        raise ArchiveExtractionError(msg)
    _enforce_entry_count(actual_count)
    return _ZipCentralDirectoryPreflight(
        actual_count,
        central_directory_offset,
        central_directory_size,
    )


def _locate_eocd_at_eof(archive: Path, file_size: int) -> tuple[int, tuple]:
    max_comment = 0xFFFF
    read_size = min(file_size, _EOCD_STRUCT.size + max_comment)
    with archive.open("rb") as handle:
        handle.seek(file_size - read_size)
        tail = handle.read()
    for pos in range(len(tail) - _EOCD_STRUCT.size, -1, -1):
        if tail[pos : pos + 4] != _EOCD_SIGNATURE:
            continue
        if len(tail) - pos < _EOCD_STRUCT.size:
            continue
        fields = _EOCD_STRUCT.unpack(tail[pos : pos + _EOCD_STRUCT.size])
        comment_length = fields[-1]
        record_end = pos + _EOCD_STRUCT.size + comment_length
        if record_end != len(tail):
            continue
        absolute_offset = file_size - read_size + pos
        return absolute_offset, fields
    msg = "malformed zip archive"
    raise ArchiveExtractionError(msg)


def _parse_zip_central_directory(
    archive: Path,
    offset: int,
    size: int,
) -> int:
    actual_count = 0
    with archive.open("rb") as handle:
        handle.seek(offset)
        end = offset + size
        while handle.tell() < end:
            header = handle.read(_CD_HEADER_STRUCT.size)
            if len(header) != _CD_HEADER_STRUCT.size:
                msg = "truncated zip central directory header"
                raise ArchiveExtractionError(msg)
            (
                signature,
                _version_made,
                _version_needed,
                flags,
                _compression,
                _mtime,
                _mdate,
                _crc,
                _compressed_size,
                _uncompressed_size,
                filename_length,
                extra_length,
                comment_length,
                disk_start,
                _internal_attr,
                _external_attr,
                _local_header_offset,
            ) = _CD_HEADER_STRUCT.unpack(header)
            if signature != _CD_HEADER_SIGNATURE:
                msg = "malformed zip central directory header"
                raise ArchiveExtractionError(msg)
            if disk_start != 0:
                msg = "multi-disk zip archives are not supported"
                raise ArchiveExtractionError(msg)
            if (
                _compressed_size == 0xFFFFFFFF
                or _uncompressed_size == 0xFFFFFFFF
                or _local_header_offset == 0xFFFFFFFF
            ):
                msg = "zip64 sentinel values are not supported"
                raise ArchiveExtractionError(msg)
            variable_length = filename_length + extra_length + comment_length
            if handle.tell() + variable_length > end:
                msg = "truncated zip central directory entry"
                raise ArchiveExtractionError(msg)
            filename = handle.read(filename_length)
            if len(filename) != filename_length:
                msg = "truncated zip central directory filename"
                raise ArchiveExtractionError(msg)
            extra = handle.read(extra_length)
            if len(extra) != extra_length:
                msg = "truncated zip central directory extra field"
                raise ArchiveExtractionError(msg)
            _reject_zip64_extra_field(extra)
            if comment_length:
                comment = handle.read(comment_length)
                if len(comment) != comment_length:
                    msg = "truncated zip central directory comment"
                    raise ArchiveExtractionError(msg)
            entry_name = _decode_zip_filename(flags, filename)
            _validate_entry_name(entry_name)
            actual_count += 1
            _enforce_entry_count(actual_count)
        if handle.tell() != end:
            msg = "zip central directory size mismatch"
            raise ArchiveExtractionError(msg)
    return actual_count


def _decode_zip_filename(flags: int, raw: bytes) -> str:
    try:
        if flags & _ZIP_UTF8_FLAG:
            return raw.decode("utf-8")
        return raw.decode("cp437")
    except UnicodeDecodeError as exc:
        msg = "zip filename is not valid text"
        raise ArchiveExtractionError(msg) from exc


def _reject_zip64_extra_field(extra: bytes) -> None:
    offset = 0
    while offset + 4 <= len(extra):
        header_id, data_size = struct.unpack_from("<HH", extra, offset)
        if header_id == _ZIP64_EXTRA_HEADER_ID:
            msg = "zip64 extra fields are not supported"
            raise ArchiveExtractionError(msg)
        offset += 4 + data_size
        if offset > len(extra):
            msg = "truncated zip extra field"
            raise ArchiveExtractionError(msg)
    if offset != len(extra):
        msg = "malformed zip extra field"
        raise ArchiveExtractionError(msg)


def extract_zip_archive(
    archive: Path,
    staging_fd: int,
    budget: _ExpandedByteBudget,
    path_registry: _EntryPathRegistry,
) -> None:
    preflight = preflight_zip_archive(archive)
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = list(zf.infolist())
            if len(infos) != preflight.entry_count:
                msg = "zip entry list size disagrees with central directory"
                raise ArchiveExtractionError(msg)
            _validate_zip_entries(infos, path_registry)
            for info in infos:
                if info.is_dir():
                    mkdir_entry(staging_fd, info.filename)
                    continue
                with zf.open(info, "r") as src:
                    write_entry_stream(
                        staging_fd,
                        info.filename,
                        src,
                        budget=budget,
                    )
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


def _validate_zip_entries(
    infos: Iterable[zipfile.ZipInfo],
    path_registry: _EntryPathRegistry,
) -> None:
    entry_count = 0
    total_bytes = 0
    for info in infos:
        entry_count += 1
        path_registry.register(info.filename)
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
    staging_fd: int,
    budget: _ExpandedByteBudget,
    path_registry: _EntryPathRegistry,
    *,
    compression: str | None,
) -> None:
    try:
        with _open_tar_stream(archive, compression) as stream:
            _parse_tar_stream(stream, staging_fd, budget, path_registry)
    except ArchiveExtractionError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        msg = f"tar extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc


@contextlib.contextmanager
def _open_tar_stream(archive: Path, compression: str | None) -> Iterator[IO[bytes]]:
    if compression is None:
        with archive.open("rb") as stream:
            yield stream
        return
    if compression == "gz":
        opener = gzip.open
    elif compression == "bz2":
        opener = bz2.open
    else:
        msg = f"unsupported tar compression: {compression}"
        raise ArchiveExtractionError(msg)
    with opener(archive, "rb") as stream:
        yield cast(IO[bytes], stream)


def _parse_tar_stream(
    stream: IO[bytes],
    staging_fd: int,
    budget: _ExpandedByteBudget,
    path_registry: _EntryPathRegistry,
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
        path_registry.register(member.name)
        if member.isdir():
            if member.size != 0:
                msg = "tar directory entry declares payload"
                raise ArchiveExtractionError(msg)
            mkdir_entry(staging_fd, member.name)
            continue
        if not member.isreg():
            msg = f"unsupported tar entry type: {member.type!r}"
            raise ArchiveExtractionError(msg)
        if member.size > budget.remaining:
            msg = f"archive exceeds expanded-byte limit ({budget.limit})"
            raise ArchiveExtractionError(msg)
        with open_entry_for_write(staging_fd, member.name) as dst_fd:
            _copy_tar_payload(stream, _FdWriter(dst_fd), member.size, budget=budget)
        _debit_tar_padding(stream, member.size, budget=budget)


class _FdWriter:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def write(self, data: bytes, /) -> int:
        return os.write(self._fd, data)


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    data = stream.read(size)
    if data is None or len(data) != size:
        msg = "unexpected end of tar archive"
        raise ArchiveExtractionError(msg)
    return data


def _copy_tar_payload(
    stream: IO[bytes],
    dst: _FdWriter,
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


def _debit_tar_padding(
    stream: IO[bytes],
    size: int,
    *,
    budget: _ExpandedByteBudget,
) -> None:
    padding = (_TAR_BLOCK - (size % _TAR_BLOCK)) % _TAR_BLOCK
    if not padding:
        return
    data = _read_exact(stream, padding)
    budget.consume(len(data))


def extract_gzip_archive(
    archive: Path,
    staging_fd: int,
    budget: _ExpandedByteBudget,
    path_registry: _EntryPathRegistry,
    *,
    out_name: str,
) -> None:
    path_registry.register(out_name)
    _enforce_entry_count(1)
    try:
        with gzip.open(archive, "rb") as src:
            write_entry_stream(staging_fd, out_name, src, budget=budget)
    except (OSError, EOFError) as exc:
        msg = f"gzip extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc


def extract_bzip2_archive(
    archive: Path,
    staging_fd: int,
    budget: _ExpandedByteBudget,
    path_registry: _EntryPathRegistry,
    *,
    out_name: str,
) -> None:
    path_registry.register(out_name)
    _enforce_entry_count(1)
    try:
        with bz2.open(archive, "rb") as src:
            write_entry_stream(staging_fd, out_name, src, budget=budget)
    except (OSError, EOFError) as exc:
        msg = f"bzip2 extraction failed: {exc}"
        raise ArchiveExtractionError(msg) from exc

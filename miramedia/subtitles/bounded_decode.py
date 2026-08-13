"""Fail-closed decoding for project-owned subtitle provider downloads."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Literal

# Real subtitle files are almost always under a few hundred KiB. Ten MiB leaves
# headroom for unusually large ASS/SSA tracks while bounding memory per request.
MAX_SUBTITLE_RESPONSE_BYTES = 10 * 1024 * 1024

# Provider ZIPs normally contain one subtitle file. Allow a modest directory of
# alternates but reject archives used as entry-count sprays.
MAX_SUBTITLE_ZIP_ENTRIES = 64

# Cap uncompressed bytes for the selected subtitle member (in-memory decode only).
MAX_SUBTITLE_MEMBER_BYTES = 5 * 1024 * 1024

# Reject members whose declared uncompressed/compressed ratio exceeds this value.
# Typical subtitle text compresses roughly 3-10x; 100x catches zip bombs while
# tolerating highly compressible but legitimate text.
MAX_SUBTITLE_COMPRESSION_RATIO = 100

_SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa", ".vtt")
_READ_CHUNK_SIZE = 16 * 1024


class ResponseReadLimitError(Exception):
    """Raised when an HTTP or file read exceeds subtitle response limits."""


class _ResponseReadLimitError(ResponseReadLimitError):
    pass


class _MemberReadLimitError(Exception):
    pass


ZipFailure = Literal["bad", "unsafe", "no_member"]
ContentKind = Literal["raw", "zip"]


@dataclass(frozen=True, slots=True)
class BoundedSubtitleContent:
    """Result of decoding a bounded subtitle download response."""

    content: bytes | None = None
    kind: ContentKind | None = None
    zip_failure: ZipFailure | None = None
    member_name: str | None = None


_ZIP_MAGIC = b"PK"


def decode_bounded_subtitle_content(data: bytes) -> BoundedSubtitleContent:
    """Decode raw or ZIP subtitle bytes with response, entry, member, and ratio limits."""
    if len(data) > MAX_SUBTITLE_RESPONSE_BYTES:
        return BoundedSubtitleContent()

    if _looks_like_zip(data):
        return _decode_zip(data)
    return BoundedSubtitleContent(content=data, kind="raw")


def _looks_like_zip(data: bytes) -> bool:
    return len(data) >= 2 and data.startswith(_ZIP_MAGIC)


def _decode_zip(data: bytes) -> BoundedSubtitleContent:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_SUBTITLE_ZIP_ENTRIES:
                return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

            for info in infos:
                if not _is_subtitle_member_name(info.filename):
                    continue
                if _zip_member_unsafe(info):
                    return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")
                try:
                    member_bytes = _read_zip_member_bounded(zf, info)
                except zipfile.BadZipFile:
                    return BoundedSubtitleContent(kind="zip", zip_failure="bad")
                except _MemberReadLimitError:
                    return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")
                except RuntimeError as exc:
                    if "encrypted" in str(exc).lower():
                        return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")
                    raise
                return BoundedSubtitleContent(
                    content=member_bytes,
                    kind="zip",
                    member_name=info.filename,
                )

            return BoundedSubtitleContent(kind="zip", zip_failure="no_member")
    except zipfile.BadZipFile:
        return BoundedSubtitleContent(kind="zip", zip_failure="bad")


def read_bounded_stream(
    stream: IO[bytes],
    max_bytes: int = MAX_SUBTITLE_RESPONSE_BYTES,
) -> bytes:
    """Read an HTTP (or file) stream up to ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _ResponseReadLimitError
        chunks.append(chunk)
    return b"".join(chunks)


def decode_bounded_zip_with_selector(
    data: bytes,
    select_member: Callable[[list[str], object], str],
    payload: object = None,
) -> BoundedSubtitleContent:
    """Decode one ZIP member chosen by a provider-specific selector."""
    if len(data) > MAX_SUBTITLE_RESPONSE_BYTES:
        return BoundedSubtitleContent()

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_SUBTITLE_ZIP_ENTRIES:
                return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

            for info in infos:
                if not info.is_dir() and _zip_member_unsafe(info):
                    return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

            names = [info.filename for info in infos]
            try:
                selected = select_member(names, payload or {})
            except ValueError:
                return BoundedSubtitleContent(kind="zip", zip_failure="no_member")

            info = next((item for item in infos if item.filename == selected), None)
            if info is None or info.is_dir():
                return BoundedSubtitleContent(kind="zip", zip_failure="no_member")
            if _zip_member_unsafe(info):
                return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

            try:
                member_bytes = _read_zip_member_bounded(zf, info)
            except zipfile.BadZipFile:
                return BoundedSubtitleContent(kind="zip", zip_failure="bad")
            except _MemberReadLimitError:
                return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")
            except RuntimeError as exc:
                if "encrypted" in str(exc).lower():
                    return BoundedSubtitleContent(kind="zip", zip_failure="unsafe")
                raise

            return BoundedSubtitleContent(
                content=member_bytes,
                kind="zip",
                member_name=info.filename,
            )
    except zipfile.BadZipFile:
        return BoundedSubtitleContent(kind="zip", zip_failure="bad")


def _is_subtitle_member_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(_SUBTITLE_EXTENSIONS)


def _zip_member_unsafe(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return True
    if info.flag_bits & 0x1:
        return True
    if info.file_size > MAX_SUBTITLE_MEMBER_BYTES:
        return True
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > MAX_SUBTITLE_COMPRESSION_RATIO:
            return True
    return False


def _read_zip_member_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with zf.open(info, "r") as member:
        while True:
            chunk = member.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SUBTITLE_MEMBER_BYTES:
                raise _MemberReadLimitError
            chunks.append(chunk)
    return b"".join(chunks)

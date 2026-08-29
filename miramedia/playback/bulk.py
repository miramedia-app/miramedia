"""Typed keys and chunking for bulk playback/viewing-sync lookups."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from miramedia.playback.schemas import MediaKind

# Conservative chunk size for bulk IN lookups (PostgreSQL parameter limits).
BULK_CHUNK_SIZE = 200


def chunked[T](items: Sequence[T], chunk_size: int) -> Iterable[Sequence[T]]:
    for offset in range(0, len(items), chunk_size):
        yield items[offset : offset + chunk_size]


@dataclass(frozen=True, slots=True)
class UserFileKey:
    user_id: UUID
    file_id: UUID
    media_kind: MediaKind


@dataclass(frozen=True, slots=True)
class UserMediaKey:
    user_id: UUID
    media_kind: MediaKind
    media_id: UUID

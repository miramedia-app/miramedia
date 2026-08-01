"""Characterization tests for media_file_inventory lookup and upsert."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Delete, Select
from sqlalchemy.dialects.postgresql.dml import Insert

from miramedia.media_inventory import (
    MediaFileInventory,
    find_inventory_path,
    upsert_inventory_path,
)


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _ExecuteResult:
    scalar: Any = None

    def scalar_one_or_none(self) -> Any:
        return self.scalar


@dataclass
class _InventorySession:
    """In-memory stand-in for inventory SELECT/DELETE/INSERT paths."""

    rows: list[MediaFileInventory] = field(default_factory=list)
    executes: list[Any] = field(default_factory=list)

    async def flush(self) -> None:
        return None

    def _lookup(
        self,
        *,
        file_id: UUID,
        kind: str,
        language: str,
    ) -> MediaFileInventory | None:
        language = language or ""
        for row in self.rows:
            if row.file_id == file_id and row.kind == kind and row.language == language:
                return row
        return None

    def _by_path(self, path: str) -> MediaFileInventory | None:
        for row in self.rows:
            if row.path == path:
                return row
        return None

    def _apply_insert(self, payload: dict[str, Any]) -> None:
        key = (payload["file_id"], payload["kind"], payload["language"])
        existing = self._lookup(
            file_id=key[0],
            kind=key[1],
            language=key[2],
        )
        if existing is not None:
            existing.path = payload["path"]
            existing.media_type = payload["media_type"]
            existing.size_bytes = payload["size_bytes"]
            existing.mtime_ns = payload["mtime_ns"]
            existing.last_seen_at = payload["last_seen_at"]
            return
        row = MediaFileInventory(
            id=payload["id"],
            file_id=payload["file_id"],
            media_type=payload["media_type"],
            kind=payload["kind"],
            language=payload["language"],
            path=payload["path"],
            size_bytes=payload["size_bytes"],
            mtime_ns=payload["mtime_ns"],
            last_seen_at=payload["last_seen_at"],
        )
        self.rows.append(row)

    async def execute(self, stmt: Any) -> _ExecuteResult:
        self.executes.append(stmt)
        if isinstance(stmt, Select):
            entity = stmt.column_descriptions[0].get("entity")
            if entity is MediaFileInventory:
                file_id = kind = language = path = None
                for criterion in stmt._where_criteria:
                    left = getattr(criterion, "left", None)
                    right = getattr(criterion, "right", None)
                    key = getattr(left, "key", None)
                    value = getattr(right, "value", None)
                    if key == "file_id":
                        file_id = value
                    elif key == "kind":
                        kind = value
                    elif key == "language":
                        language = value or ""
                    elif key == "path":
                        path = value
                if path is not None:
                    return _ExecuteResult(self._by_path(path))
                if file_id is not None and kind is not None and language is not None:
                    return _ExecuteResult(
                        self._lookup(
                            file_id=file_id,
                            kind=kind,
                            language=language or "",
                        )
                    )
            return _ExecuteResult(None)
        if isinstance(stmt, Delete):
            if getattr(stmt.table, "name", None) == MediaFileInventory.__tablename__:
                self.rows = [
                    row for row in self.rows if not self._row_matches(stmt, row)
                ]
            return _ExecuteResult(None)
        if isinstance(stmt, Insert):
            payload = dict(stmt.compile().params)
            self._apply_insert(payload)
            return _ExecuteResult(None)
        return _ExecuteResult(None)

    def _row_matches(self, stmt: Delete, row: MediaFileInventory) -> bool:
        if not stmt._where_criteria:
            return True
        for criterion in stmt._where_criteria:
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            key = getattr(left, "key", None)
            value = getattr(right, "value", None)
            if key is None:
                continue
            if getattr(row, key) != value:
                return False
        return True


def test_find_inventory_path_miss_returns_none() -> None:
    session = _InventorySession()

    result = _run(
        find_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=uuid.uuid4(),
            kind="video",
            language="",
        )
    )

    assert result is None


def test_upsert_then_find_returns_stored_path(tmp_path: Path) -> None:
    session = _InventorySession()
    file_id = uuid.uuid4()
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video-bytes")

    _run(
        upsert_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
            language="",
            media_type="movie",
            path=media_path,
        )
    )
    found = _run(
        find_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
            language="",
        )
    )

    assert found == media_path


def test_upsert_replaces_path_for_same_lookup_key(tmp_path: Path) -> None:
    session = _InventorySession()
    file_id = uuid.uuid4()
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    _run(
        upsert_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
            path=first,
        )
    )
    _run(
        upsert_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
            path=second,
        )
    )
    found = _run(
        find_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
        )
    )

    assert found == second
    lookup_rows = [
        row
        for row in session.rows
        if row.file_id == file_id and row.kind == "video" and row.language == ""
    ]
    assert len(lookup_rows) == 1
    assert lookup_rows[0].path == str(second)


def test_different_kind_and_language_coexist_for_same_file_id(
    tmp_path: Path,
) -> None:
    session = _InventorySession()
    file_id = uuid.uuid4()
    video = tmp_path / "episode.mkv"
    subtitle = tmp_path / "episode.en.srt"
    video.write_bytes(b"video")
    subtitle.write_bytes(b"subs")

    _run(
        upsert_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
            path=video,
        )
    )
    _run(
        upsert_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="subtitle",
            language="en",
            path=subtitle,
        )
    )

    found_video = _run(
        find_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
        )
    )
    found_sub = _run(
        find_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="subtitle",
            language="en",
        )
    )

    assert found_video == video
    assert found_sub == subtitle
    assert len(session.rows) == 2


def test_find_inventory_path_deletes_stale_row_when_file_missing(
    tmp_path: Path,
) -> None:
    session = _InventorySession()
    file_id = uuid.uuid4()
    media_path = tmp_path / "gone.mkv"
    media_path.write_bytes(b"gone")
    stat = media_path.stat()
    session.rows.append(
        MediaFileInventory(
            id=uuid.uuid4(),
            file_id=file_id,
            kind="video",
            language="",
            path=str(media_path),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            last_seen_at=datetime.now(UTC),
        )
    )
    media_path.unlink()

    result = _run(
        find_inventory_path(
            session,  # type: ignore[arg-type]
            file_id=file_id,
            kind="video",
        )
    )

    assert result is None
    assert session.rows == []

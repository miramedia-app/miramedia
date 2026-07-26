"""Tests for arr-ID mapping helpers."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from miramedia.subtitles.arr_ids import (
    ArrIdMap,
    _uuids_missing_from_map,
    get_or_create_arr_ids,
    resolve_arr_id,
)


def test_uuids_missing_from_map_dedupes_and_preserves_order() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    third = uuid.uuid4()
    existing = {first: 1, third: 3}

    missing = _uuids_missing_from_map([first, second, first, third], existing)

    assert missing == [second]


@pytest.mark.anyio
async def test_get_or_create_arr_ids_returns_existing_without_insert() -> None:
    entity_uuid = uuid.uuid4()
    row = ArrIdMap(id=42, entity_type="series", entity_uuid=entity_uuid)
    db = MagicMock()
    execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=iter([row])))
    )
    db.execute = execute

    result = await get_or_create_arr_ids(db, "series", [entity_uuid])

    assert result == {entity_uuid: 42}
    assert execute.await_count == 1


@pytest.mark.anyio
async def test_get_or_create_arr_ids_inserts_missing_then_reselects() -> None:
    existing_uuid = uuid.uuid4()
    missing_uuid = uuid.uuid4()
    existing_row = ArrIdMap(id=1, entity_type="episode", entity_uuid=existing_uuid)
    inserted_row = ArrIdMap(id=2, entity_type="episode", entity_uuid=missing_uuid)

    select_existing = MagicMock(scalars=MagicMock(return_value=iter([existing_row])))
    select_missing = MagicMock(scalars=MagicMock(return_value=iter([inserted_row])))
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[select_existing, None, select_missing])

    result = await get_or_create_arr_ids(db, "episode", [existing_uuid, missing_uuid])

    assert result == {existing_uuid: 1, missing_uuid: 2}
    assert db.execute.await_count == 3


@pytest.mark.anyio
async def test_get_or_create_arr_ids_empty_input() -> None:
    db = MagicMock()
    db.execute = AsyncMock()

    assert await get_or_create_arr_ids(db, "movie", []) == {}
    db.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_arr_id_returns_uuid() -> None:
    entity_uuid = uuid.uuid4()
    db = MagicMock()
    db.scalar = AsyncMock(return_value=entity_uuid)

    result = await resolve_arr_id(db, "movie_file", 99)

    assert result == entity_uuid
    db.scalar.assert_awaited_once()


@pytest.mark.anyio
async def test_resolve_arr_id_returns_none_when_missing() -> None:
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)

    assert await resolve_arr_id(db, "series", 404) is None

"""Characterization tests for miramedia.pagination helpers."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from miramedia.pagination import (
    decode_cursor,
    encode_cursor,
    parse_datetime,
    parse_uuid,
)


def test_encode_decode_cursor_roundtrip() -> None:
    payload = {"id": str(uuid4()), "created_at": "2026-01-15T12:00:00"}
    cursor = encode_cursor(payload)
    assert decode_cursor(cursor) == payload


def test_decode_cursor_none_or_empty_returns_none() -> None:
    assert decode_cursor(None) is None
    assert decode_cursor("") is None


def test_decode_cursor_invalid_returns_none() -> None:
    assert decode_cursor("not-a-valid-cursor") is None
    assert decode_cursor("!!!") is None


def test_parse_datetime_valid_iso() -> None:
    value = "2026-07-30T18:50:00"
    parsed = parse_datetime(value)
    assert parsed == datetime.fromisoformat(value)


def test_parse_datetime_none_or_empty_returns_none() -> None:
    assert parse_datetime(None) is None
    assert parse_datetime("") is None


def test_parse_datetime_invalid_returns_none() -> None:
    assert parse_datetime("not-a-date") is None


def test_parse_uuid_valid() -> None:
    value = uuid4()
    assert parse_uuid(str(value)) == value


def test_parse_uuid_none_or_empty_returns_none() -> None:
    assert parse_uuid(None) is None
    assert parse_uuid("") is None


def test_parse_uuid_invalid_returns_none() -> None:
    assert parse_uuid("not-a-uuid") is None


def test_encode_cursor_strips_padding() -> None:
    cursor = encode_cursor({"n": 1})
    assert "=" not in cursor
    assert decode_cursor(cursor) == {"n": 1}

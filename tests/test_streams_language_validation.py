"""Regression: subtitle language path segments are validated before disk/DB access."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import EpisodeId
from miramedia.streams.router import (
    _reject_unsafe_subtitle_language,
    _resolve_subtitle_file,
)
from tests.test_stream_file_binding import stream_client

PREFIX = "/api/v1/streams"


def _run(coro):
    return asyncio.run(coro)


def test_reject_unsafe_subtitle_language_blocks_path_segments() -> None:
    for bad in ("../../etc", "en/..", "a b", "en\n"):
        with pytest.raises(HTTPException) as exc_info:
            _reject_unsafe_subtitle_language(bad)
        assert exc_info.value.status_code == 404


@pytest.mark.parametrize("language", ["en", "en-US", "por"])
def test_resolve_subtitle_file_accepts_valid_language_tags(
    language: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    sub_file = media_root / f"show.{language}.vtt"
    sub_file.write_text("WEBVTT\n", encoding="utf-8")
    file_id = uuid.uuid4()
    remember = AsyncMock()
    find_indexed = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        find_indexed,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._find_first_subtitle_file",
        lambda _directory, _stems, lang: sub_file if lang == language else None,
    )

    result = _run(
        _resolve_subtitle_file(
            MagicMock(),
            file_id=file_id,
            media_type="movie",
            language=language,
            directory=media_root,
            stems=["show"],
            allowed_roots=[media_root],
        )
    )

    assert result == sub_file
    remember.assert_awaited_once()


def test_resolve_subtitle_file_rejects_unsafe_language_before_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    find_indexed = AsyncMock()
    remember = AsyncMock()
    monkeypatch.setattr(
        "miramedia.streams.router._find_indexed_file",
        find_indexed,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._remember_indexed_file",
        remember,
    )

    with pytest.raises(HTTPException) as exc_info:
        _run(
            _resolve_subtitle_file(
                MagicMock(),
                file_id=uuid.uuid4(),
                media_type="movie",
                language="../../etc",
                directory=media_root,
                stems=["show"],
                allowed_roots=[media_root],
            )
        )

    assert exc_info.value.status_code == 404
    find_indexed.assert_not_awaited()
    remember.assert_not_awaited()


def test_movie_subtitle_route_rejects_invalid_language_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie_id = MovieId(uuid.uuid4())
    file_id = uuid.uuid4()
    resolve = AsyncMock()
    monkeypatch.setattr("miramedia.streams.router._resolve_subtitle_file", resolve)

    with stream_client() as client:
        response = client.get(
            f"{PREFIX}/subtitles/movies/{movie_id}/a%20b?file_id={file_id}"
        )

    assert response.status_code == 422
    resolve.assert_not_awaited()


def test_episode_subtitle_route_rejects_invalid_language_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_id = EpisodeId(uuid.uuid4())
    file_id = uuid.uuid4()
    resolve = AsyncMock()
    monkeypatch.setattr("miramedia.streams.router._resolve_subtitle_file", resolve)

    with stream_client() as client:
        response = client.get(
            f"{PREFIX}/subtitles/episodes/{episode_id}/a%20b?file_id={file_id}"
        )

    assert response.status_code == 422
    resolve.assert_not_awaited()

"""Unit tests for IMDb ID resolution in the requests service."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from miramedia.requests.schemas import MediaType
from miramedia.requests.service import _resolve_imdb_id
from tests.fakes import run_async


@dataclass
class _StubMetadata:
    imdb_id: str


@dataclass
class _RecordingProvider:
    movie_imdb_id: str = "tt999"
    show_imdb_id: str = "tt888"
    recorded_threads: list[threading.Thread] = field(default_factory=list)

    def get_movie_metadata(self, _external_id: str) -> _StubMetadata:
        self.recorded_threads.append(threading.current_thread())
        return _StubMetadata(imdb_id=self.movie_imdb_id)

    def get_show_metadata(self, _external_id: str) -> _StubMetadata:
        self.recorded_threads.append(threading.current_thread())
        return _StubMetadata(imdb_id=self.show_imdb_id)


class _RaisingProvider:
    def get_movie_metadata(self, _external_id: str) -> _StubMetadata:
        msg = "provider failure"
        raise RuntimeError(msg)

    def get_show_metadata(self, _external_id: str) -> _StubMetadata:
        msg = "provider failure"
        raise RuntimeError(msg)


def test_resolve_imdb_id_movie_offloads_to_thread(monkeypatch) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        lambda _name: provider,
    )

    async def _run() -> None:
        loop_thread = threading.current_thread()
        result = await _resolve_imdb_id(MediaType.movie, "123", "tmdb")
        assert result == "tt999"
        assert len(provider.recorded_threads) == 1
        assert provider.recorded_threads[0] is not loop_thread

    run_async(_run())


def test_resolve_imdb_id_show_offloads_to_thread(monkeypatch) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        lambda _name: provider,
    )

    async def _run() -> None:
        loop_thread = threading.current_thread()
        result = await _resolve_imdb_id(MediaType.show, "456", "tvdb")
        assert result == "tt888"
        assert len(provider.recorded_threads) == 1
        assert provider.recorded_threads[0] is not loop_thread

    run_async(_run())


def test_resolve_imdb_id_provider_error_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        lambda _name: _RaisingProvider(),
    )

    async def _run() -> None:
        result = await _resolve_imdb_id(MediaType.movie, "123", "tmdb")
        assert result is None

    run_async(_run())


def test_resolve_imdb_id_native_short_circuits_without_provider(
    monkeypatch,
) -> None:
    resolve = MagicMock()
    monkeypatch.setattr(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        resolve,
    )

    async def _run() -> None:
        result = await _resolve_imdb_id(MediaType.movie, "tt123", "native")
        assert result == "tt123"

    run_async(_run())
    resolve.assert_not_called()

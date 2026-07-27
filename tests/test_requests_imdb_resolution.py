"""Unit tests for IMDb ID resolution in the requests service."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestCreate,
    MediaType,
    RequestStatus,
)
from miramedia.requests.service import RequestService, _resolve_imdb_id
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


@dataclass
class _ProviderWithRepository:
    repository: object
    created: list[MediaRequestCreate] = field(default_factory=list)

    async def create_request(
        self,
        data: MediaRequestCreate,
        requested_by_id: UUID,
        auto_approve: bool,
    ) -> MediaRequest:
        del requested_by_id, auto_approve
        self.created.append(data)
        return MediaRequest(
            media_type=data.media_type,
            title=data.title,
            external_id=data.external_id,
            imdb_id=data.imdb_id,
            metadata_provider=data.metadata_provider,
            status=RequestStatus.pending,
        )

    async def set_imdb_id(self, request_id: object, imdb_id: str) -> MediaRequest:
        del request_id
        return MediaRequest(
            media_type=MediaType.movie,
            title="Healed",
            external_id="123",
            imdb_id=imdb_id,
            metadata_provider="tmdb",
            status=RequestStatus.pending,
        )


def test_create_request_releases_session_before_metadata_lookup(
    monkeypatch,
) -> None:
    calls: list[str] = []
    provider = _RecordingProvider()
    repo = SimpleNamespace(db=MagicMock())
    request_provider = _ProviderWithRepository(repository=repo)

    async def _release(_db: object) -> None:
        calls.append("release")

    async def _tracked_resolve(
        media_type: MediaType, external_id: str, provider_name: str
    ) -> str | None:
        calls.append("resolve")
        return await _resolve_imdb_id(media_type, external_id, provider_name)

    monkeypatch.setattr(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        lambda _name: provider,
    )
    monkeypatch.setattr(
        "miramedia.requests.service.MiraMediaConfig",
        lambda: SimpleNamespace(
            requests=SimpleNamespace(auto_approve_users=False),
        ),
    )
    monkeypatch.setattr(
        "miramedia.requests.service.release_session_before_external_io",
        _release,
    )
    monkeypatch.setattr(
        "miramedia.requests.service._resolve_imdb_id",
        _tracked_resolve,
    )

    service = RequestService(request_provider)
    data = MediaRequestCreate(
        media_type=MediaType.movie,
        title="Needs IMDb",
        external_id="12345",
        metadata_provider="tmdb",
    )

    run_async(service.create_request(data, uuid4(), is_superuser=False))

    assert calls.index("release") < calls.index("resolve")
    assert request_provider.created[0].imdb_id == "tt999"


def test_heal_missing_imdb_id_releases_session_before_metadata_lookup(
    monkeypatch,
) -> None:
    calls: list[str] = []
    provider = _RecordingProvider()
    repo = SimpleNamespace(db=MagicMock())
    request_provider = _ProviderWithRepository(repository=repo)

    async def _release(_db: object) -> None:
        calls.append("release")

    async def _tracked_resolve(
        media_type: MediaType, external_id: str, provider_name: str
    ) -> str | None:
        calls.append("resolve")
        return await _resolve_imdb_id(media_type, external_id, provider_name)

    monkeypatch.setattr(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        lambda _name: provider,
    )
    monkeypatch.setattr(
        "miramedia.requests.service.release_session_before_external_io",
        _release,
    )
    monkeypatch.setattr(
        "miramedia.requests.service._resolve_imdb_id",
        _tracked_resolve,
    )

    service = RequestService(request_provider)
    request = MediaRequest(
        media_type=MediaType.show,
        title="Missing IMDb",
        external_id="67890",
        metadata_provider="tvdb",
        status=RequestStatus.pending,
    )

    healed = run_async(service.heal_missing_imdb_id(request))

    assert calls.index("release") < calls.index("resolve")
    assert healed.imdb_id == "tt888"

"""Behavioral tests for ``fulfill_approved_requests_task``."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import miramedia.scheduler as scheduler
from miramedia.requests.schemas import MediaType, RequestStatus
from tests.fakes.config import fake_scheduler_config
from tests.fakes.repositories import make_movie, make_show
from tests.fakes.scheduler import (
    FakeMovieService,
    FakeShowService,
    TrackingRequestService,
    bg_movie_service_factory,
    bg_request_service_factory,
    bg_show_service_factory,
    make_request,
    native_provider,
)


def _run(coro) -> None:
    asyncio.run(coro)


def _patch_fulfill_common(monkeypatch, *, requests_enabled: bool = True) -> None:
    cfg = fake_scheduler_config(requests_enabled=requests_enabled)
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.MiraMediaConfig",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.build_seerr_client",
        lambda: None,
    )
    monkeypatch.setattr(
        "miramedia.scheduler_tasks.media.resolve_metadata_provider",
        lambda _name: native_provider(),
    )


def test_requests_disabled_returns_without_opening_services(monkeypatch) -> None:
    opened = False

    async def _fail_bg_request_service():
        nonlocal opened
        opened = True
        msg = "bg_request_service should not be called"
        raise AssertionError(msg)

    _patch_fulfill_common(monkeypatch, requests_enabled=False)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service", _fail_bg_request_service
    )

    _run(scheduler.fulfill_approved_requests_task())

    assert opened is False


def test_no_approved_requests_skips_downloads(monkeypatch) -> None:
    request_service = TrackingRequestService(approved=[])
    movie_service = FakeMovieService(make_movie())
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    assert movie_service.add_movie_calls == []
    auto_download.assert_not_called()


def test_fresh_approved_movie_marks_downloading_then_downloaded(monkeypatch) -> None:
    request = make_request(
        media_type=MediaType.movie,
        status=RequestStatus.approved,
        external_id="tt1234567",
    )
    movie = make_movie()
    request_service = TrackingRequestService(approved=[request])
    movie_service = FakeMovieService(movie, downloaded=True)
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    auto_download.assert_awaited_once_with(movie.id)
    assert request_service.mark_downloading_ids == [request.id]
    assert request_service.mark_downloaded_ids == [request.id]
    assert movie_service.add_movie_calls == [("tt1234567", native_provider())]


def test_downloading_movie_skips_redispatch_but_still_marks_downloaded(
    monkeypatch,
) -> None:
    request = make_request(
        media_type=MediaType.movie,
        status=RequestStatus.downloading,
        external_id="tt7654321",
    )
    movie = make_movie()
    request_service = TrackingRequestService(approved=[request])
    movie_service = FakeMovieService(movie, downloaded=True)
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    auto_download.assert_not_called()
    assert request_service.mark_downloading_ids == []
    assert request_service.mark_downloaded_ids == [request.id]


def test_native_non_tt_without_imdb_skips_request(monkeypatch) -> None:
    request = make_request(
        media_type=MediaType.movie,
        status=RequestStatus.approved,
        external_id="tvdb-123",
        imdb_id=None,
        metadata_provider="native",
    )
    request_service = TrackingRequestService(approved=[request])
    request_service.heal_result = request.model_copy(update={"imdb_id": None})
    movie_service = FakeMovieService(make_movie())
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    assert request_service.heal_calls == 1
    assert movie_service.add_movie_calls == []
    auto_download.assert_not_called()
    assert request_service.mark_downloading_ids == []
    assert request_service.mark_downloaded_ids == []


def test_add_movie_exception_isolated_second_request_still_processed(
    monkeypatch,
) -> None:
    first = make_request(title="First", external_id="tt1111111")
    second = make_request(title="Second", external_id="tt2222222")
    movie = make_movie()
    request_service = TrackingRequestService(approved=[first, second])
    movie_service = FakeMovieService(
        movie,
        downloaded=True,
        add_raises=RuntimeError("add failed"),
    )
    auto_download = AsyncMock()
    call_count = 0

    async def _add_then_succeed(*, external_id, metadata_provider):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = "add failed"
            raise RuntimeError(err)
        movie_service.add_movie_calls.append((external_id, metadata_provider))
        return movie

    movie_service.add_movie = _add_then_succeed  # type: ignore[method-assign]

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_movie_service",
        bg_movie_service_factory(movie_service),
    )
    monkeypatch.setattr(
        "miramedia.movies.service._try_auto_download_movie_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    assert call_count == 2
    assert auto_download.await_count == 1
    assert request_service.mark_downloaded_ids == [second.id]


def test_show_with_downloaded_episode_marks_downloaded(monkeypatch) -> None:
    show = make_show()
    episode_id = show.seasons[0].episodes[0].id
    request = make_request(
        media_type=MediaType.show,
        status=RequestStatus.approved,
        external_id="tt9999999",
    )
    request_service = TrackingRequestService(approved=[request])
    show_service = FakeShowService(show, downloaded_episodes={episode_id})
    auto_download = AsyncMock()

    _patch_fulfill_common(monkeypatch)
    monkeypatch.setattr(
        "miramedia.background_services.bg_request_service",
        bg_request_service_factory(request_service),
    )
    monkeypatch.setattr(
        "miramedia.background_services.bg_show_service",
        bg_show_service_factory(show_service),
    )
    monkeypatch.setattr(
        "miramedia.shows.service._try_auto_download_show_id_impl",
        auto_download,
    )

    _run(scheduler.fulfill_approved_requests_task())

    auto_download.assert_awaited_once_with(show.id)
    assert request_service.mark_downloading_ids == [request.id]
    assert request_service.mark_downloaded_ids == [request.id]

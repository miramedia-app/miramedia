"""Characterization tests for CompositeRequestProvider write-through behavior."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest

from miramedia.requests.backends.composite import CompositeRequestProvider
from miramedia.requests.backends.seerr import SeerrRequest
from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestCreate,
    MediaRequestId,
    MediaType,
    RequestStatus,
)
from tests.fakes.services import run_async


def _request(
    *,
    seerr_request_id: int | None = None,
    seerr_media_id: int | None = None,
    status: RequestStatus = RequestStatus.approved,
    media_type: MediaType = MediaType.movie,
    season_number: int | None = None,
    title: str = "Test Title",
) -> MediaRequest:
    return MediaRequest(
        id=MediaRequestId(uuid.uuid4()),
        media_type=media_type,
        title=title,
        external_id="tt1234567",
        status=status,
        seerr_request_id=seerr_request_id,
        seerr_media_id=seerr_media_id,
        season_number=season_number,
    )


@dataclass
class StubNative:
    approve_result: MediaRequest | None = None
    reject_result: MediaRequest | None = None
    mark_downloaded_result: MediaRequest | None = None
    create_result: MediaRequest | None = None
    delete_calls: list[MediaRequestId] = field(default_factory=list)
    call_log: list[str] = field(default_factory=list)

    async def create_request(
        self, data: MediaRequestCreate, requested_by_id: UUID, auto_approve: bool
    ) -> MediaRequest:
        del data, requested_by_id, auto_approve
        self.call_log.append("create_request")
        assert self.create_result is not None
        return self.create_result

    async def approve_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        del decided_by_id
        self.call_log.append(f"approve_request:{request_id}")
        assert self.approve_result is not None
        return self.approve_result

    async def reject_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        del decided_by_id
        self.call_log.append(f"reject_request:{request_id}")
        assert self.reject_result is not None
        return self.reject_result

    async def delete_request(self, request_id: MediaRequestId) -> None:
        self.call_log.append(f"delete_request:{request_id}")
        self.delete_calls.append(request_id)

    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        self.call_log.append(f"mark_downloaded:{request_id}")
        assert self.mark_downloaded_result is not None
        return self.mark_downloaded_result


@dataclass
class StubSeerrClient:
    approved_ids: list[int] = field(default_factory=list)
    declined_ids: list[int] = field(default_factory=list)
    deleted_ids: list[int] = field(default_factory=list)
    marked_media_ids: list[int] = field(default_factory=list)
    created: list[tuple[str, int, list[int] | None]] = field(default_factory=list)
    create_result: SeerrRequest | None = None
    raise_on: str | None = None
    raise_exc: Exception = field(default_factory=lambda: httpx.HTTPError("boom"))

    async def approve(self, request_id: int) -> None:
        if self.raise_on == "approve":
            raise self.raise_exc
        self.approved_ids.append(request_id)

    async def decline(self, request_id: int) -> None:
        if self.raise_on == "decline":
            raise self.raise_exc
        self.declined_ids.append(request_id)

    async def delete_request(self, request_id: int) -> None:
        if self.raise_on == "delete_request":
            raise self.raise_exc
        self.deleted_ids.append(request_id)

    async def mark_media_available(self, media_id: int) -> None:
        if self.raise_on == "mark_media_available":
            raise self.raise_exc
        self.marked_media_ids.append(media_id)

    async def create_request(
        self,
        media_type: str,
        tmdb_id: int,
        *,
        seasons: list[int] | None = None,
    ) -> SeerrRequest | None:
        if self.raise_on == "create_request":
            raise self.raise_exc
        self.created.append((media_type, tmdb_id, seasons))
        if self.create_result is not None:
            return self.create_result
        return SeerrRequest(
            request_id=9001,
            media_id=8001,
            media_type=media_type,
            request_status=1,
            media_status=1,
            tmdb_id=tmdb_id,
            imdb_id=None,
            seasons=seasons or [],
        )


@dataclass
class StubRepository:
    rows: dict[MediaRequestId, MediaRequest] = field(default_factory=dict)
    call_log: list[str] = field(default_factory=list)
    update_calls: list[tuple[MediaRequestId, dict[str, Any]]] = field(
        default_factory=list
    )

    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        self.call_log.append(f"get_request:{request_id}")
        return self.rows[request_id]

    async def update_request(
        self, request_id: MediaRequestId, **kwargs: Any
    ) -> MediaRequest:
        self.call_log.append(f"update_request:{request_id}")
        self.update_calls.append((request_id, dict(kwargs)))
        row = self.rows[request_id]
        updated = row.model_copy(update=kwargs)
        self.rows[request_id] = updated
        return updated


def _provider(
    native: StubNative,
    repository: StubRepository,
    client: StubSeerrClient | None,
) -> CompositeRequestProvider:
    return CompositeRequestProvider(native, repository, client)


def _invoke_write_through(
    provider: CompositeRequestProvider,
    method: str,
    request_id: MediaRequestId,
) -> MediaRequest | None:
    if method == "mark_downloaded":
        return run_async(provider.mark_downloaded(request_id))
    decider = uuid.uuid4()
    if method == "approve_request":
        return run_async(provider.approve_request(request_id, decider))
    if method == "reject_request":
        return run_async(provider.reject_request(request_id, decider))
    msg = f"unsupported write-through method: {method}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Table-driven write-through: approve / reject / mark_downloaded
# ---------------------------------------------------------------------------

_WRITETHROUGH_CASES = [
    pytest.param(
        "approve_request",
        "approve",
        "approved_ids",
        "seerr_request_id",
        42,
        id="approve",
    ),
    pytest.param(
        "reject_request",
        "decline",
        "declined_ids",
        "seerr_request_id",
        42,
        id="reject",
    ),
    pytest.param(
        "mark_downloaded",
        "mark_media_available",
        "marked_media_ids",
        "seerr_media_id",
        99,
        id="mark_downloaded",
    ),
]


@pytest.mark.parametrize(
    ("method", "client_method", "client_attr", "id_field", "linked_id"),
    _WRITETHROUGH_CASES,
)
def test_write_through_seerr_healthy(
    method: str,
    client_method: str,
    client_attr: str,
    id_field: str,
    linked_id: int,
) -> None:
    del client_method
    request_id = MediaRequestId(uuid.uuid4())
    native_result = _request(**{id_field: linked_id})
    native = StubNative()
    if method == "approve_request":
        native.approve_result = native_result
    elif method == "reject_request":
        native.reject_result = native_result
    else:
        native.mark_downloaded_result = native_result

    client = StubSeerrClient()
    provider = _provider(native, StubRepository(), client)

    result = _invoke_write_through(provider, method, request_id)

    assert result is native_result
    assert getattr(client, client_attr) == [linked_id]


@pytest.mark.parametrize(
    ("method", "client_method", "client_attr", "id_field", "linked_id"),
    _WRITETHROUGH_CASES,
)
def test_write_through_seerr_http_error_still_returns_native(
    method: str,
    client_method: str,
    client_attr: str,
    id_field: str,
    linked_id: int,
) -> None:
    del client_attr
    request_id = MediaRequestId(uuid.uuid4())
    native_result = _request(**{id_field: linked_id})
    native = StubNative()
    if method == "approve_request":
        native.approve_result = native_result
    elif method == "reject_request":
        native.reject_result = native_result
    else:
        native.mark_downloaded_result = native_result

    client = StubSeerrClient(raise_on=client_method)
    provider = _provider(native, StubRepository(), client)

    result = _invoke_write_through(provider, method, request_id)

    assert result is native_result


@pytest.mark.parametrize(
    ("method", "client_method", "client_attr", "id_field"),
    [
        ("approve_request", "approve", "approved_ids", "seerr_request_id"),
        ("reject_request", "decline", "declined_ids", "seerr_request_id"),
        (
            "mark_downloaded",
            "mark_media_available",
            "marked_media_ids",
            "seerr_media_id",
        ),
    ],
)
def test_write_through_skips_client_when_link_id_none(
    method: str,
    client_method: str,
    client_attr: str,
    id_field: str,
) -> None:
    del client_method
    request_id = MediaRequestId(uuid.uuid4())
    native_result = _request(**{id_field: None})
    native = StubNative()
    if method == "approve_request":
        native.approve_result = native_result
    elif method == "reject_request":
        native.reject_result = native_result
    else:
        native.mark_downloaded_result = native_result

    client = StubSeerrClient()
    provider = _provider(native, StubRepository(), client)

    result = _invoke_write_through(provider, method, request_id)

    assert result is native_result
    assert getattr(client, client_attr) == []


@pytest.mark.parametrize(
    ("method", "client_method", "id_field", "linked_id"),
    [
        ("approve_request", "approve", "seerr_request_id", 42),
        ("reject_request", "decline", "seerr_request_id", 42),
        ("mark_downloaded", "mark_media_available", "seerr_media_id", 99),
    ],
)
def test_write_through_propagates_non_http_errors(
    method: str,
    client_method: str,
    id_field: str,
    linked_id: int,
) -> None:
    request_id = MediaRequestId(uuid.uuid4())
    native_result = _request(**{id_field: linked_id})
    native = StubNative()
    if method == "approve_request":
        native.approve_result = native_result
    elif method == "reject_request":
        native.reject_result = native_result
    else:
        native.mark_downloaded_result = native_result

    client = StubSeerrClient(
        raise_on=client_method,
        raise_exc=ValueError("not an httpx error"),
    )
    provider = _provider(native, StubRepository(), client)

    with pytest.raises(ValueError, match="not an httpx error"):
        _invoke_write_through(provider, method, request_id)


# ---------------------------------------------------------------------------
# delete_request write-through
# ---------------------------------------------------------------------------


def test_delete_write_through_seerr_healthy() -> None:
    request_id = MediaRequestId(uuid.uuid4())
    repository = StubRepository(
        rows={request_id: _request(seerr_request_id=55)},
    )
    native = StubNative()
    client = StubSeerrClient()
    provider = _provider(native, repository, client)

    run_async(provider.delete_request(request_id))

    assert client.deleted_ids == [55]
    assert native.delete_calls == [request_id]


def test_delete_write_through_seerr_http_error_does_not_raise() -> None:
    request_id = MediaRequestId(uuid.uuid4())
    repository = StubRepository(
        rows={request_id: _request(seerr_request_id=55)},
    )
    native = StubNative()
    client = StubSeerrClient(raise_on="delete_request")
    provider = _provider(native, repository, client)

    run_async(provider.delete_request(request_id))

    assert native.delete_calls == [request_id]


def test_delete_write_through_skips_client_when_seerr_id_none() -> None:
    request_id = MediaRequestId(uuid.uuid4())
    repository = StubRepository(
        rows={request_id: _request(seerr_request_id=None)},
    )
    native = StubNative()
    client = StubSeerrClient()
    provider = _provider(native, repository, client)

    run_async(provider.delete_request(request_id))

    assert client.deleted_ids == []
    assert native.delete_calls == [request_id]


def test_delete_write_through_propagates_non_http_errors() -> None:
    request_id = MediaRequestId(uuid.uuid4())
    repository = StubRepository(
        rows={request_id: _request(seerr_request_id=55)},
    )
    native = StubNative()
    client = StubSeerrClient(
        raise_on="delete_request",
        raise_exc=ValueError("not an httpx error"),
    )
    provider = _provider(native, repository, client)

    with pytest.raises(ValueError, match="not an httpx error"):
        run_async(provider.delete_request(request_id))


def test_delete_captures_seerr_id_before_native_delete() -> None:
    request_id = MediaRequestId(uuid.uuid4())
    order: list[str] = []
    repository = StubRepository(
        rows={request_id: _request(seerr_request_id=77)},
    )
    native = StubNative()
    client = StubSeerrClient()

    original_get = repository.get_request
    original_delete = native.delete_request

    async def tracked_get(request_id: MediaRequestId) -> MediaRequest:
        order.append("get_request")
        return await original_get(request_id)

    async def tracked_delete(request_id: MediaRequestId) -> None:
        order.append("native_delete")
        await original_delete(request_id)

    repository.get_request = tracked_get  # type: ignore[method-assign]
    native.delete_request = tracked_delete  # type: ignore[method-assign]
    provider = _provider(native, repository, client)

    run_async(provider.delete_request(request_id))

    assert order == ["get_request", "native_delete"]


# ---------------------------------------------------------------------------
# create_request / _push_new
# ---------------------------------------------------------------------------


def test_create_request_with_client_none_skips_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _request(title="No Seerr")
    native = StubNative(create_result=created)
    repository = StubRepository(rows={created.id: created})

    async def _push_new_should_not_run(_request: MediaRequest) -> None:
        msg = "_push_new must not run when client is None"
        raise AssertionError(msg)

    monkeypatch.setattr(CompositeRequestProvider, "_push_new", _push_new_should_not_run)

    provider = _provider(native, repository, None)
    data = MediaRequestCreate(
        media_type=MediaType.movie,
        title="No Seerr",
        external_id="tt9999999",
    )

    result = run_async(provider.create_request(data, uuid.uuid4(), auto_approve=False))

    assert result is created


def test_push_new_resolve_tmdb_http_error_returns_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(status=RequestStatus.pending)
    repository = StubRepository(rows={request.id: request})
    client = StubSeerrClient()

    async def _resolve_raises(*_args: object, **_kwargs: object) -> int:
        err = httpx.HTTPError("tmdb lookup failed")
        raise err

    monkeypatch.setattr(
        "miramedia.requests.sync.resolve_tmdb",
        _resolve_raises,
    )

    run_async(_provider(StubNative(), repository, client)._push_new(request))

    assert client.created == []


def test_push_new_approved_request_calls_create_approve_and_updates_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(status=RequestStatus.approved, media_type=MediaType.movie)
    repository = StubRepository(rows={request.id: request})
    client = StubSeerrClient(
        create_result=SeerrRequest(
            request_id=111,
            media_id=222,
            media_type="movie",
            request_status=1,
            media_status=1,
            tmdb_id=555,
            imdb_id=None,
            seasons=[],
        )
    )

    async def _resolve_ok(*_args: object, **_kwargs: object) -> int:
        return 555

    monkeypatch.setattr(
        "miramedia.requests.sync.resolve_tmdb",
        _resolve_ok,
    )

    run_async(_provider(StubNative(), repository, client)._push_new(request))

    assert client.created == [("movie", 555, None)]
    assert client.approved_ids == [111]
    assert repository.update_calls == [
        (request.id, {"seerr_request_id": 111, "seerr_media_id": 222}),
    ]


def test_push_new_client_http_error_mid_push_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(status=RequestStatus.pending)
    repository = StubRepository(rows={request.id: request})
    client = StubSeerrClient(raise_on="create_request")

    async def _resolve_ok(*_args: object, **_kwargs: object) -> int:
        return 555

    monkeypatch.setattr(
        "miramedia.requests.sync.resolve_tmdb",
        _resolve_ok,
    )

    run_async(_provider(StubNative(), repository, client)._push_new(request))

    assert repository.update_calls == []


# ---------------------------------------------------------------------------
# client=None with stale Seerr link ids (Seerr disabled after link)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "id_field", "linked_id"),
    [
        ("approve_request", "seerr_request_id", 42),
        ("reject_request", "seerr_request_id", 42),
        ("mark_downloaded", "seerr_media_id", 99),
    ],
)
def test_write_through_client_none_with_linked_id_skips_seerr(
    method: str,
    id_field: str,
    linked_id: int,
) -> None:
    request_id = MediaRequestId(uuid.uuid4())
    native_result = _request(**{id_field: linked_id})
    native = StubNative()
    if method == "approve_request":
        native.approve_result = native_result
    elif method == "reject_request":
        native.reject_result = native_result
    else:
        native.mark_downloaded_result = native_result

    provider = _provider(native, StubRepository(), None)

    result = _invoke_write_through(provider, method, request_id)

    assert result is native_result
    assert native.call_log == [f"{method}:{request_id}"]


def test_delete_write_through_client_none_with_linked_seerr_id_skips_seerr() -> None:
    request_id = MediaRequestId(uuid.uuid4())
    repository = StubRepository(
        rows={request_id: _request(seerr_request_id=55)},
    )
    native = StubNative()
    provider = _provider(native, repository, None)

    run_async(provider.delete_request(request_id))

    assert native.delete_calls == [request_id]
    assert repository.call_log == [f"get_request:{request_id}"]

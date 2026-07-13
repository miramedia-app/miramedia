"""Behavioral tests for Seerr sync and native request service transitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from miramedia.requests.backends.abstract_request_provider import (
    AbstractRequestProvider,
)
from miramedia.requests.backends.seerr import (
    SEERR_MEDIA_AVAILABLE,
    SEERR_MEDIA_PARTIALLY_AVAILABLE,
    SEERR_REQ_APPROVED,
    SEERR_REQ_DECLINED,
    SEERR_REQ_PENDING,
    SeerrRequest,
)
from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestCreate,
    MediaRequestId,
    MediaRequestUpdate,
    MediaType,
    RequestSource,
    RequestStatus,
)
from miramedia.requests.service import RequestService
from miramedia.requests.sync import SeerrSyncService, map_seerr_status
from tests.fakes.repositories import FakeRequestRepository
from tests.fakes.scheduler import make_request
from tests.fakes.services import run_async


def _seerr_request(
    *,
    request_id: int = 1,
    media_id: int = 10,
    media_type: str = "movie",
    request_status: int = SEERR_REQ_PENDING,
    media_status: int = 1,
    tmdb_id: int | None = 100,
    imdb_id: str | None = "tt1111111",
    seasons: list[int] | None = None,
) -> SeerrRequest:
    return SeerrRequest(
        request_id=request_id,
        media_id=media_id,
        media_type=media_type,
        request_status=request_status,
        media_status=media_status,
        tmdb_id=tmdb_id,
        imdb_id=imdb_id,
        seasons=seasons or [],
    )


@dataclass
class FakeSeerrClient:
    requests: list[SeerrRequest] = field(default_factory=list)
    title_imdb: dict[tuple[str, int], tuple[str, str | None]] = field(
        default_factory=dict
    )
    title_imdb_raises: Exception | None = None
    create_results: dict[int, SeerrRequest | None] = field(default_factory=dict)
    create_raises: dict[int, Exception] = field(default_factory=dict)
    find_tmdb: dict[str, tuple[int, str] | None] = field(default_factory=dict)
    created: list[tuple[str, int, list[int] | None]] = field(default_factory=list)
    approved_ids: list[int] = field(default_factory=list)
    declined_ids: list[int] = field(default_factory=list)
    _create_seq: int = 0

    async def iter_requests(self) -> list[SeerrRequest]:
        return list(self.requests)

    async def resolve_title_imdb(
        self, media_type: str, tmdb_id: int
    ) -> tuple[str, str | None]:
        if self.title_imdb_raises is not None:
            raise self.title_imdb_raises
        return self.title_imdb.get(
            (media_type, tmdb_id), (f"{media_type} {tmdb_id}", None)
        )

    async def find_tmdb_by_imdb(self, imdb_id: str) -> tuple[int, str] | None:
        return self.find_tmdb.get(imdb_id)

    async def create_request(
        self,
        media_type: str,
        tmdb_id: int,
        *,
        seasons: list[int] | None = None,
    ) -> SeerrRequest | None:
        self.created.append((media_type, tmdb_id, seasons))
        self._create_seq += 1
        if self._create_seq in self.create_raises:
            raise self.create_raises[self._create_seq]
        if self._create_seq in self.create_results:
            return self.create_results[self._create_seq]
        return SeerrRequest(
            request_id=9000 + self._create_seq,
            media_id=8000 + self._create_seq,
            media_type=media_type,
            request_status=SEERR_REQ_PENDING,
            media_status=1,
            tmdb_id=tmdb_id,
            imdb_id=None,
            seasons=seasons or [],
        )

    async def approve(self, request_id: int) -> None:
        self.approved_ids.append(request_id)

    async def decline(self, request_id: int) -> None:
        self.declined_ids.append(request_id)


# ---------------------------------------------------------------------------
# map_seerr_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_status", "media_status", "expected"),
    [
        (SEERR_REQ_PENDING, SEERR_MEDIA_AVAILABLE, RequestStatus.downloaded),
        (SEERR_REQ_APPROVED, SEERR_MEDIA_PARTIALLY_AVAILABLE, RequestStatus.downloaded),
        (SEERR_REQ_DECLINED, 1, RequestStatus.rejected),
        (SEERR_REQ_APPROVED, 1, RequestStatus.approved),
        (SEERR_REQ_PENDING, 1, RequestStatus.pending),
        (99, 1, RequestStatus.pending),  # unknown → pending default
    ],
)
def test_map_seerr_status_branches(
    request_status: int, media_status: int, expected: RequestStatus
) -> None:
    req = _seerr_request(request_status=request_status, media_status=media_status)
    assert map_seerr_status(req) == expected


def test_map_seerr_status_media_available_beats_declined() -> None:
    """Media availability takes precedence over request declined."""
    req = _seerr_request(
        request_status=SEERR_REQ_DECLINED,
        media_status=SEERR_MEDIA_AVAILABLE,
    )
    assert map_seerr_status(req) == RequestStatus.downloaded


# ---------------------------------------------------------------------------
# pull / _upsert_one
# ---------------------------------------------------------------------------


def test_pull_creates_new_request_with_mapped_status() -> None:
    repo = FakeRequestRepository()
    client = FakeSeerrClient(
        requests=[
            _seerr_request(
                request_id=42,
                media_id=7,
                request_status=SEERR_REQ_APPROVED,
                imdb_id="tt2222222",
                tmdb_id=555,
            )
        ],
        title_imdb={("movie", 555): ("Synced Movie", "tt2222222")},
    )
    synced = run_async(SeerrSyncService(repo, client).pull())

    assert synced == 1
    assert len(repo.upsert_calls) == 1
    row = repo.by_seerr_id[42]
    assert row.title == "Synced Movie"
    assert row.status == RequestStatus.approved
    assert row.source == RequestSource.seerr
    assert row.external_id == "tt2222222"
    assert row.tmdb_id == 555
    assert row.seerr_media_id == 7


def test_pull_updates_existing_by_seerr_request_id_without_duplicating() -> None:
    existing_id = MediaRequestId(uuid.uuid4())
    repo = FakeRequestRepository()
    repo.seed(
        MediaRequest(
            id=existing_id,
            media_type=MediaType.movie,
            title="Old Title",
            external_id="tt2222222",
            imdb_id="tt2222222",
            status=RequestStatus.pending,
            source=RequestSource.seerr,
            tmdb_id=555,
            seerr_request_id=42,
            seerr_media_id=7,
        )
    )
    client = FakeSeerrClient(
        requests=[
            _seerr_request(
                request_id=42,
                media_id=7,
                request_status=SEERR_REQ_APPROVED,
                media_status=SEERR_MEDIA_AVAILABLE,
                imdb_id="tt2222222",
                tmdb_id=555,
            )
        ],
        title_imdb={("movie", 555): ("New Title", "tt2222222")},
    )

    synced = run_async(SeerrSyncService(repo, client).pull())

    assert synced == 1
    assert len(repo.by_id) == 1
    assert len(repo.by_seerr_id) == 1
    row = repo.by_seerr_id[42]
    assert row.id == existing_id
    assert row.title == "New Title"
    assert row.status == RequestStatus.downloaded


def test_pull_exception_on_one_request_does_not_abort_batch() -> None:
    """Current behavior: per-item exceptions are isolated; remaining still sync."""
    repo = FakeRequestRepository()
    good = _seerr_request(request_id=2, tmdb_id=200, imdb_id="tt200")
    bad = _seerr_request(request_id=1, tmdb_id=100, imdb_id=None)
    client = FakeSeerrClient(
        requests=[bad, good],
        title_imdb={("movie", 200): ("Good Movie", "tt200")},
    )

    async def _boom(
        media_type: str,  # noqa: ARG001 — matches SeerrClient signature
        tmdb_id: int,
    ) -> tuple[str, str | None]:
        if tmdb_id == 100:
            msg = "title lookup exploded"
            raise RuntimeError(msg)
        return ("Good Movie", "tt200")

    client.resolve_title_imdb = _boom  # type: ignore[method-assign]

    synced = run_async(SeerrSyncService(repo, client).pull())

    assert synced == 1
    assert 2 in repo.by_seerr_id
    assert 1 not in repo.by_seerr_id


def test_pull_failed_title_lookup_still_upserts_with_fallback_title() -> None:
    """resolve_title_imdb HTTP-style fallback does not skip the mirror write."""
    repo = FakeRequestRepository()
    client = FakeSeerrClient(
        requests=[_seerr_request(request_id=9, tmdb_id=999, imdb_id=None)],
        # default FakeSeerrClient fallback: ("movie 999", None)
    )

    synced = run_async(SeerrSyncService(repo, client).pull())

    assert synced == 1
    row = repo.by_seerr_id[9]
    assert row.title == "movie 999"
    assert row.external_id == ""
    assert row.imdb_id is None


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def test_push_sends_only_unsynced_native_with_resolvable_tmdb() -> None:
    repo = FakeRequestRepository()
    should_push = make_request(
        title="Push Me",
        status=RequestStatus.approved,
        external_id="tt3333333",
        imdb_id="tt3333333",
    )
    should_push = should_push.model_copy(
        update={"source": RequestSource.native, "tmdb_id": 333}
    )
    already_synced = make_request(
        title="Synced",
        status=RequestStatus.approved,
        external_id="tt4444444",
    ).model_copy(
        update={
            "source": RequestSource.native,
            "tmdb_id": 444,
            "seerr_request_id": 50,
        }
    )
    rejected = make_request(
        title="Rejected",
        status=RequestStatus.rejected,
        external_id="tt5555555",
    ).model_copy(update={"source": RequestSource.native, "tmdb_id": 555})
    unresolvable = make_request(
        title="No TMDB",
        status=RequestStatus.pending,
        external_id="xyz",
        imdb_id=None,
        metadata_provider="native",
    ).model_copy(update={"source": RequestSource.native, "tmdb_id": None})

    for row in (should_push, already_synced, rejected, unresolvable):
        repo.seed(row)

    client = FakeSeerrClient()
    pushed = run_async(SeerrSyncService(repo, client).push())

    assert pushed == 1
    assert client.created == [("movie", 333, None)]
    assert client.approved_ids == [9001]
    updated = repo.by_id[should_push.id]
    assert updated.seerr_request_id == 9001
    assert updated.seerr_media_id == 8001


def test_push_client_http_error_isolates_remaining_requests() -> None:
    """Current behavior: httpx.HTTPError on one item does not abort the batch."""
    repo = FakeRequestRepository()
    first = make_request(title="First", external_id="tt1111111", imdb_id="tt1111111")
    first = first.model_copy(
        update={
            "source": RequestSource.native,
            "tmdb_id": 111,
            "status": RequestStatus.pending,
        }
    )
    second = make_request(title="Second", external_id="tt2222222", imdb_id="tt2222222")
    second = second.model_copy(
        update={
            "source": RequestSource.native,
            "tmdb_id": 222,
            "status": RequestStatus.pending,
        }
    )
    # Stable iteration order via insertion
    repo.seed(first)
    repo.seed(second)

    client = FakeSeerrClient(
        create_raises={
            1: httpx.HTTPError("upstream failed"),
        }
    )
    pushed = run_async(SeerrSyncService(repo, client).push())

    assert pushed == 1
    assert len(client.created) == 2
    assert repo.by_id[first.id].seerr_request_id is None
    assert repo.by_id[second.id].seerr_request_id == 9002


def test_push_approves_or_declines_based_on_native_status() -> None:
    repo = FakeRequestRepository()
    approved = make_request(
        title="Approved",
        status=RequestStatus.approved,
        external_id="tt1",
        imdb_id="tt1",
    ).model_copy(update={"source": RequestSource.native, "tmdb_id": 1})
    # rejected is filtered out by list_native_unsynced — seed pending then
    # exercise decline via a row that passes the gate: only pending/approved.
    # Pin: rejected native rows are not pushed at all.
    rejected = make_request(
        title="Rejected",
        status=RequestStatus.rejected,
        external_id="tt2",
        imdb_id="tt2",
    ).model_copy(update={"source": RequestSource.native, "tmdb_id": 2})
    repo.seed(approved)
    repo.seed(rejected)

    client = FakeSeerrClient()
    pushed = run_async(SeerrSyncService(repo, client).push())

    assert pushed == 1
    assert client.approved_ids == [9001]
    assert client.declined_ids == []


# ---------------------------------------------------------------------------
# RequestService transitions
# ---------------------------------------------------------------------------


@dataclass
class TrackingRequestProvider(AbstractRequestProvider):
    created: list[tuple[MediaRequestCreate, UUID, bool]] = field(default_factory=list)
    approved: list[tuple[MediaRequestId, UUID]] = field(default_factory=list)
    rejected: list[tuple[MediaRequestId, UUID]] = field(default_factory=list)
    downloading: list[MediaRequestId] = field(default_factory=list)
    rows: dict[MediaRequestId, MediaRequest] = field(default_factory=dict)

    async def create_request(
        self, data: MediaRequestCreate, requested_by_id: UUID, auto_approve: bool
    ) -> MediaRequest:
        self.created.append((data, requested_by_id, auto_approve))
        row = MediaRequest(
            media_type=data.media_type,
            title=data.title,
            external_id=data.external_id,
            imdb_id=data.imdb_id,
            metadata_provider=data.metadata_provider,
            requested_by_id=requested_by_id,
            status=(RequestStatus.approved if auto_approve else RequestStatus.pending),
        )
        self.rows[row.id] = row
        return row

    async def list_requests(
        self,
        status: RequestStatus | None = None,  # noqa: ARG002
        media_type: MediaType | None = None,  # noqa: ARG002
        requested_by_id: UUID | None = None,  # noqa: ARG002
    ) -> list[MediaRequest]:
        return list(self.rows.values())

    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        return self.rows[request_id]

    async def update_request(
        self,
        request_id: MediaRequestId,
        data: MediaRequestUpdate,  # noqa: ARG002
        user_id: UUID,  # noqa: ARG002
    ) -> MediaRequest:
        return self.rows[request_id]

    async def approve_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        self.approved.append((request_id, decided_by_id))
        row = self.rows[request_id].model_copy(
            update={
                "status": RequestStatus.approved,
                "decided_by_id": decided_by_id,
            }
        )
        self.rows[request_id] = row
        return row

    async def reject_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        self.rejected.append((request_id, decided_by_id))
        row = self.rows[request_id].model_copy(
            update={
                "status": RequestStatus.rejected,
                "decided_by_id": decided_by_id,
            }
        )
        self.rows[request_id] = row
        return row

    async def delete_request(self, request_id: MediaRequestId) -> None:
        self.rows.pop(request_id, None)

    async def get_pending_count(self):
        from miramedia.requests.schemas import MediaRequestCount

        return MediaRequestCount(
            pending=sum(
                1 for r in self.rows.values() if r.status == RequestStatus.pending
            )
        )

    async def get_approved_not_downloaded(self) -> list[MediaRequest]:
        return [
            r
            for r in self.rows.values()
            if r.status in (RequestStatus.approved, RequestStatus.downloading)
        ]

    async def mark_downloading(self, request_id: MediaRequestId) -> MediaRequest:
        self.downloading.append(request_id)
        row = self.rows[request_id].model_copy(
            update={"status": RequestStatus.downloading}
        )
        self.rows[request_id] = row
        return row

    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        row = self.rows[request_id].model_copy(
            update={"status": RequestStatus.downloaded}
        )
        self.rows[request_id] = row
        return row

    async def set_imdb_id(
        self, request_id: MediaRequestId, imdb_id: str
    ) -> MediaRequest:
        row = self.rows[request_id].model_copy(update={"imdb_id": imdb_id})
        self.rows[request_id] = row
        return row


def test_request_service_create_pending_for_non_superuser(monkeypatch) -> None:
    monkeypatch.setattr(
        "miramedia.requests.service.MiraMediaConfig",
        lambda: SimpleNamespace(
            requests=SimpleNamespace(auto_approve_users=False),
        ),
    )
    provider = TrackingRequestProvider()
    service = RequestService(provider)
    user_id = uuid.uuid4()
    data = MediaRequestCreate(
        media_type=MediaType.movie,
        title="New Movie",
        external_id="tt7777777",
        imdb_id="tt7777777",
        metadata_provider="native",
    )

    result = run_async(service.create_request(data, user_id, is_superuser=False))

    assert result.status == RequestStatus.pending
    assert len(provider.created) == 1
    assert provider.created[0][2] is False


def test_request_service_create_auto_approves_superuser(monkeypatch) -> None:
    monkeypatch.setattr(
        "miramedia.requests.service.MiraMediaConfig",
        lambda: SimpleNamespace(
            requests=SimpleNamespace(auto_approve_users=False),
        ),
    )
    provider = TrackingRequestProvider()
    service = RequestService(provider)
    user_id = uuid.uuid4()
    data = MediaRequestCreate(
        media_type=MediaType.movie,
        title="Admin Movie",
        external_id="tt8888888",
        imdb_id="tt8888888",
    )

    result = run_async(service.create_request(data, user_id, is_superuser=True))

    assert result.status == RequestStatus.approved
    assert provider.created[0][2] is True


def test_request_service_approve_reject_mark_downloading() -> None:
    provider = TrackingRequestProvider()
    pending = MediaRequest(
        media_type=MediaType.movie,
        title="Decide Me",
        external_id="tt9999999",
        status=RequestStatus.pending,
    )
    provider.rows[pending.id] = pending
    service = RequestService(provider)
    decider = uuid.uuid4()

    approved = run_async(service.approve_request(pending.id, decider))
    assert approved.status == RequestStatus.approved
    assert provider.approved == [(pending.id, decider)]

    rejected_id = MediaRequestId(uuid.uuid4())
    provider.rows[rejected_id] = MediaRequest(
        id=rejected_id,
        media_type=MediaType.movie,
        title="No",
        external_id="tt0000001",
        status=RequestStatus.pending,
    )
    rejected = run_async(service.reject_request(rejected_id, decider))
    assert rejected.status == RequestStatus.rejected
    assert provider.rejected == [(rejected_id, decider)]

    downloading = run_async(service.mark_downloading(pending.id))
    assert downloading.status == RequestStatus.downloading
    assert provider.downloading == [pending.id]

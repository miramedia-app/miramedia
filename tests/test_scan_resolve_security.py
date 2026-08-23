"""Regression tests for scan resolve path containment and cache gating."""

from __future__ import annotations

import asyncio
import types
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miramedia.exceptions import ConflictError, NotFoundError
from miramedia.imports.repository import ScanClaimOutcome, ScanClaimResult
from miramedia.imports.scan_resolve import validate_scan_resolve_request
from miramedia.imports.schemas import ResolveRequest
from miramedia.media_paths import (
    PathNotDirectoryError,
    PathNotFoundError,
    PathOutsideRootsError,
    library_roots_for_media_type,
    resolve_path_within_roots,
)
from miramedia.torrents.schemas import MediaType

PREFIX = "/api/v1/imports"


def _scan_body(
    directory: str,
    *,
    media_type: MediaType = MediaType.show,
    media_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "kind": "scan",
        "id": directory,
        "media_type": media_type.value,
        "media_id": str(media_id or uuid.uuid4()),
    }


@pytest.fixture
def library_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    show_root = tmp_path / "shows"
    movie_root = tmp_path / "movies"
    show_root.mkdir()
    movie_root.mkdir()

    misc = types.SimpleNamespace(
        show_directory=show_root,
        movie_directory=movie_root,
        show_libraries=(),
        movie_libraries=(),
    )
    monkeypatch.setattr(
        "miramedia.media_paths.MiraMediaConfig",
        lambda: types.SimpleNamespace(misc=misc),
    )
    return show_root, movie_root


@pytest.fixture
def named_library_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    default_show = tmp_path / "default-shows"
    named_show = tmp_path / "named-shows"
    default_movie = tmp_path / "default-movies"
    named_movie = tmp_path / "named-movies"
    for path in (default_show, named_show, default_movie, named_movie):
        path.mkdir()

    misc = types.SimpleNamespace(
        show_directory=default_show,
        movie_directory=default_movie,
        show_libraries=(types.SimpleNamespace(name="Anime", path=str(named_show)),),
        movie_libraries=(types.SimpleNamespace(name="4K", path=str(named_movie)),),
    )
    monkeypatch.setattr(
        "miramedia.media_paths.MiraMediaConfig",
        lambda: types.SimpleNamespace(misc=misc),
    )
    return named_show, named_movie


def test_nested_show_directory_accepted(library_roots: tuple[Path, Path]) -> None:
    show_root, _ = library_roots
    nested = show_root / "nested" / "show"
    nested.mkdir(parents=True)
    resolved = resolve_path_within_roots(
        nested, library_roots_for_media_type(MediaType.show), require_directory=True
    )
    assert resolved == nested.resolve()


def test_nested_movie_directory_accepted(library_roots: tuple[Path, Path]) -> None:
    _, movie_root = library_roots
    nested = movie_root / "nested" / "movie"
    nested.mkdir(parents=True)
    resolved = resolve_path_within_roots(
        nested, library_roots_for_media_type(MediaType.movie), require_directory=True
    )
    assert resolved == nested.resolve()


def test_named_show_library_accepted(named_library_roots: tuple[Path, Path]) -> None:
    named_show, _ = named_library_roots
    target = named_show / "title"
    target.mkdir()
    resolved = resolve_path_within_roots(
        target, library_roots_for_media_type(MediaType.show), require_directory=True
    )
    assert resolved == target.resolve()


def test_named_movie_library_accepted(named_library_roots: tuple[Path, Path]) -> None:
    _, named_movie = named_library_roots
    target = named_movie / "title"
    target.mkdir()
    resolved = resolve_path_within_roots(
        target, library_roots_for_media_type(MediaType.movie), require_directory=True
    )
    assert resolved == target.resolve()


def test_sibling_outside_root_rejected(library_roots: tuple[Path, Path]) -> None:
    show_root, _ = library_roots
    outside = show_root.parent / "outside"
    outside.mkdir()
    with pytest.raises(PathOutsideRootsError):
        resolve_path_within_roots(
            outside,
            library_roots_for_media_type(MediaType.show),
            require_directory=True,
        )


def test_absolute_path_outside_roots_rejected(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    foreign = show_root.parent / "foreign"
    foreign.mkdir()
    with pytest.raises(PathOutsideRootsError):
        resolve_path_within_roots(
            foreign,
            library_roots_for_media_type(MediaType.show),
            require_directory=True,
        )


def test_missing_path_rejected(library_roots: tuple[Path, Path]) -> None:
    show_root, _ = library_roots
    with pytest.raises(PathNotFoundError):
        resolve_path_within_roots(
            show_root / "missing",
            library_roots_for_media_type(MediaType.show),
            require_directory=True,
        )


def test_file_rejected(library_roots: tuple[Path, Path]) -> None:
    show_root, _ = library_roots
    file_path = show_root / "file.mkv"
    file_path.touch()
    with pytest.raises(PathNotDirectoryError):
        resolve_path_within_roots(
            file_path,
            library_roots_for_media_type(MediaType.show),
            require_directory=True,
        )


def test_symlink_escape_rejected(library_roots: tuple[Path, Path]) -> None:
    show_root, _ = library_roots
    outside = show_root.parent / "outside-target"
    outside.mkdir()
    link = show_root / "escape"
    link.symlink_to(outside)
    with pytest.raises(PathOutsideRootsError):
        resolve_path_within_roots(
            link,
            library_roots_for_media_type(MediaType.show),
            require_directory=True,
        )


def test_symlinked_root_accepts_nested_content(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    real_root = show_root.parent / "real-shows"
    real_root.mkdir()
    nested = real_root / "nested"
    nested.mkdir()
    root_link = show_root.parent / "shows-link"
    root_link.symlink_to(real_root)
    resolved = resolve_path_within_roots(
        nested,
        [root_link],
        require_directory=True,
    )
    assert resolved == nested.resolve()


def test_validate_scan_resolve_rejects_missing_cache_row(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    target = show_root / "orphan"
    target.mkdir()
    repo = MagicMock()
    repo.get_scan_cache_entry = AsyncMock(return_value=None)
    body = ResolveRequest.model_validate(_scan_body(str(target)))

    async def _run() -> None:
        with pytest.raises(NotFoundError):
            await validate_scan_resolve_request(repo, body)

    asyncio.run(_run())


def test_validate_scan_resolve_rejects_symlink_alias_without_exact_row(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    real = show_root / "real"
    real.mkdir()
    alias = show_root / "alias"
    alias.symlink_to(real)
    repo = MagicMock()
    repo.get_scan_cache_entry = AsyncMock(return_value=None)
    body = ResolveRequest.model_validate(_scan_body(str(alias)))

    async def _run() -> None:
        with pytest.raises(NotFoundError):
            await validate_scan_resolve_request(repo, body)
        repo.get_scan_cache_entry.assert_awaited_once_with(str(alias))

    asyncio.run(_run())


def test_validate_scan_resolve_returns_exact_request_key(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    target = show_root / "cached"
    target.mkdir()
    repo = MagicMock()
    repo.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(target),
            "status": "pending",
            "detected_name": "Cached",
            "library_name": "Default",
            "media_type_hint": "show",
        }
    )
    body = ResolveRequest.model_validate(_scan_body(str(target)))

    async def _run() -> None:
        cache_key = await validate_scan_resolve_request(repo, body)
        assert cache_key == str(target)

    asyncio.run(_run())


@contextmanager
def imports_resolve_client(
    *,
    claim_outcome: ScanClaimOutcome | None = None,
) -> Generator[tuple[TestClient, AsyncMock, AsyncMock]]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.imports.dependencies import get_imports_repository
    from miramedia.main import app

    outcome = claim_outcome or ScanClaimOutcome(
        ScanClaimResult.claimed, claim_token="issued-claim-id"
    )
    repo = MagicMock()
    repo.claim_scan_cache_row = AsyncMock(return_value=outcome)
    repo.compensate_scan_cache_claim = AsyncMock(return_value=True)

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.is_superuser = True
        return user

    async def _repo_dep() -> Any:
        return repo

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_imports_repository] = _repo_dep

    task_mock = AsyncMock()
    with (
        patch(
            "miramedia.imports.router.validate_scan_resolve_request",
            new=AsyncMock(return_value="/safe/show"),
        ),
        patch("miramedia.imports.tasks.resolve_import_task") as task_module,
    ):
        task_module.kiq = task_mock
        try:
            client = TestClient(app, raise_server_exceptions=False)
            yield client, task_mock, repo
        finally:
            app.dependency_overrides.clear()


def test_resolve_route_does_not_dispatch_when_claim_rejected() -> None:
    with imports_resolve_client(
        claim_outcome=ScanClaimOutcome(ScanClaimResult.not_found)
    ) as (
        client,
        task_mock,
        repo,
    ):
        response = client.post(f"{PREFIX}/resolve", json=_scan_body("/safe/show"))
    assert response.status_code == 404
    task_mock.assert_not_awaited()
    repo.claim_scan_cache_row.assert_awaited_once_with("/safe/show", media_type="show")


def test_resolve_route_dispatches_exact_cache_key_after_claim() -> None:
    with imports_resolve_client() as (client, task_mock, repo):
        response = client.post(f"{PREFIX}/resolve", json=_scan_body("/safe/show"))
    assert response.status_code == 202
    task_mock.assert_awaited_once()
    payload = task_mock.await_args.args[0]
    assert payload["body"]["id"] == "/safe/show"
    assert payload["scan_claim_token"] == "issued-claim-id"
    repo.claim_scan_cache_row.assert_awaited_once_with("/safe/show", media_type="show")


def test_resolve_route_compensates_when_broker_dispatch_raises() -> None:
    with imports_resolve_client() as (client, task_mock, repo):
        task_mock.side_effect = RuntimeError("broker down")
        response = client.post(f"{PREFIX}/resolve", json=_scan_body("/safe/show"))
    assert response.status_code == 503
    repo.compensate_scan_cache_claim.assert_awaited_once_with(
        "/safe/show",
        claim_token="issued-claim-id",
        error="Failed to queue import task. Press Import to retry.",
    )


def test_alias_request_does_not_claim_canonical_cache_row(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    real = show_root / "real"
    real.mkdir()
    alias = show_root / "alias"
    alias.symlink_to(real)
    repo = MagicMock()
    repo.get_scan_cache_entry = AsyncMock(return_value=None)
    repo.claim_scan_cache_row = AsyncMock()
    body = ResolveRequest.model_validate(_scan_body(str(alias)))

    async def _run() -> None:
        with pytest.raises(NotFoundError):
            await validate_scan_resolve_request(repo, body)

    asyncio.run(_run())
    repo.claim_scan_cache_row.assert_not_awaited()


def test_resolve_scan_requires_queued_exact_row_before_import(
    library_roots: tuple[Path, Path],
) -> None:
    from miramedia.imports.service import ImportsService

    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    show_service = MagicMock()
    show_service.import_show_from_directory = AsyncMock(return_value=True)
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(target),
            "status": "pending",
            "media_type_hint": "show",
            "claim_token": "token-a",
            "worker_started_at": "2026-07-13T00:00:00+00:00",
        }
    )
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=show_service,
        movie_service=MagicMock(),
    )
    body = ResolveRequest.model_validate(_scan_body(str(target)))

    async def _run() -> None:
        with pytest.raises(ConflictError):
            await service.resolve_manual_scan(body, claim_token="token-a")

    asyncio.run(_run())
    show_service.import_show_from_directory.assert_not_awaited()


def test_resolve_scan_rejects_missing_media_type_hint(
    library_roots: tuple[Path, Path],
) -> None:
    from miramedia.imports.service import ImportsService

    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(target),
            "status": "queued",
            "claim_token": "token-a",
            "worker_started_at": "2026-07-13T00:00:00+00:00",
        }
    )
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )
    body = ResolveRequest.model_validate(_scan_body(str(target)))

    async def _run() -> None:
        with pytest.raises(ConflictError):
            await service.resolve_manual_scan(body, claim_token="token-a")

    asyncio.run(_run())


def test_resolve_scan_rejects_missing_worker_started_marker(
    library_roots: tuple[Path, Path],
) -> None:
    from miramedia.imports.service import ImportsService

    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(target),
            "status": "queued",
            "media_type_hint": "show",
            "claim_token": "token-a",
        }
    )
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=MagicMock(),
        movie_service=MagicMock(),
    )
    body = ResolveRequest.model_validate(_scan_body(str(target)))

    async def _run() -> None:
        with pytest.raises(ConflictError):
            await service.resolve_manual_scan(body, claim_token="token-a")

    asyncio.run(_run())


def test_resolve_scan_does_not_import_outside_root(
    library_roots: tuple[Path, Path],
) -> None:
    from miramedia.imports.service import ImportsService

    show_root, _ = library_roots
    outside = show_root.parent / "outside"
    outside.mkdir()
    show_service = MagicMock()
    show_service.import_show_from_directory = AsyncMock(return_value=True)
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(outside),
            "status": "queued",
            "media_type_hint": "show",
            "claim_token": "token-a",
            "worker_started_at": "2026-07-13T00:00:00+00:00",
        }
    )
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=show_service,
        movie_service=MagicMock(),
    )
    body = ResolveRequest.model_validate(
        {
            "kind": "scan",
            "id": str(outside),
            "media_type": "show",
            "media_id": str(uuid.uuid4()),
        }
    )

    async def _run() -> None:
        with pytest.raises(NotFoundError):
            await service.resolve_manual_scan(body, claim_token="token-a")

    asyncio.run(_run())
    show_service.import_show_from_directory.assert_not_awaited()


def test_resolve_scan_honors_lost_terminal_cas(
    library_roots: tuple[Path, Path],
) -> None:
    from miramedia.imports.service import ImportsService

    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    show_service = MagicMock()
    show_service.get_show_by_id = AsyncMock(
        return_value=MagicMock(id=uuid.uuid4(), name="Show")
    )
    show_service.import_show_from_directory = AsyncMock(return_value=True)
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(target),
            "status": "queued",
            "media_type_hint": "show",
            "claim_token": "token-a",
            "worker_started_at": "2026-07-13T00:00:00+00:00",
        }
    )
    repository.complete_manual_scan_import = AsyncMock(return_value=False)
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=show_service,
        movie_service=MagicMock(),
    )
    body = ResolveRequest.model_validate(_scan_body(str(target)))

    async def _run() -> None:
        with (
            patch(
                "miramedia.imports.followup.run_post_import_completion",
                new=AsyncMock(),
            ) as followup_mock,
            pytest.raises(ConflictError),
        ):
            await service.resolve_manual_scan(body, claim_token="token-a")
        followup_mock.assert_not_awaited()

    asyncio.run(_run())
    show_service.import_show_from_directory.assert_awaited_once()
    repository.complete_manual_scan_import.assert_awaited_once()


def test_resolve_scan_releases_session_before_path_resolution(
    library_roots: tuple[Path, Path],
) -> None:
    from miramedia.imports.service import ImportsService

    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    show_service = MagicMock()
    show_service.get_show_by_id = AsyncMock(
        return_value=MagicMock(id=uuid.uuid4(), name="Show")
    )
    show_service.import_show_from_directory = AsyncMock(return_value=True)
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value={
            "directory": str(target),
            "status": "queued",
            "media_type_hint": "show",
            "claim_token": "token-a",
            "worker_started_at": "2026-07-13T00:00:00+00:00",
        }
    )
    repository.complete_manual_scan_import = AsyncMock(return_value=True)
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=show_service,
        movie_service=MagicMock(),
    )
    body = ResolveRequest.model_validate(_scan_body(str(target)))
    events: list[str] = []

    async def _release(_db: object) -> None:
        events.append("release")

    async def _import(**_kwargs: object) -> bool:
        events.append("import")
        return True

    show_service.import_show_from_directory.side_effect = _import

    async def _run() -> None:
        with (
            patch(
                "miramedia.database.release_session_before_external_io",
                side_effect=_release,
            ),
            patch(
                "miramedia.imports.followup.run_post_import_completion",
                new=AsyncMock(),
            ),
        ):
            await service.resolve_manual_scan(body, claim_token="token-a")

    asyncio.run(_run())
    assert events.index("release") < events.index("import")
    repository.get_scan_cache_entry.assert_awaited_once()

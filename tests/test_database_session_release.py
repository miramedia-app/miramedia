"""DB session release ordering for manual scan resolve."""

from __future__ import annotations

import asyncio
import types
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from miramedia.imports.schemas import ResolveRequest
from miramedia.imports.service import ImportsService
from miramedia.torrents.schemas import MediaType

_RELEASE_PATCH = "miramedia.database.release_session_before_external_io"


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


def _queued_cache_row(directory: str, *, media_type: str = "show") -> dict[str, Any]:
    return {
        "directory": directory,
        "status": "queued",
        "media_type_hint": media_type,
        "claim_token": "token-a",
        "worker_started_at": "2026-07-13T00:00:00+00:00",
    }


def _resolve_service(
    *,
    directory: str,
    media_type: MediaType,
    show_service: MagicMock | None = None,
    movie_service: MagicMock | None = None,
) -> tuple[ImportsService, MagicMock, MagicMock, MagicMock]:
    repository = MagicMock()
    repository.get_scan_cache_entry = AsyncMock(
        return_value=_queued_cache_row(directory, media_type=media_type.value)
    )
    repository.complete_manual_scan_import = AsyncMock(return_value=True)
    repository.fail_manual_scan_import = AsyncMock(return_value=True)
    show_service = show_service or MagicMock()
    movie_service = movie_service or MagicMock()
    service = ImportsService(
        repository=repository,
        torrent_service=MagicMock(),
        show_service=show_service,
        movie_service=movie_service,
    )
    return service, repository, show_service, movie_service


def test_resolve_scan_releases_before_show_import_io(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    media_id = uuid.uuid4()
    show = types.SimpleNamespace(id=media_id, name="Show")
    show_service = MagicMock()
    show_service.get_show_by_id = AsyncMock(return_value=show)
    show_service.import_show_from_directory = AsyncMock(return_value=True)
    service, repository, _, _ = _resolve_service(
        directory=str(target),
        media_type=MediaType.show,
        show_service=show_service,
    )
    body = ResolveRequest.model_validate(_scan_body(str(target), media_id=media_id))
    events: list[str] = []

    async def _release(_db: object) -> None:
        events.append("release")

    async def _import(**_kwargs: object) -> bool:
        events.append("import")
        return True

    show_service.import_show_from_directory.side_effect = _import

    async def _run() -> None:
        with (
            patch(_RELEASE_PATCH, side_effect=_release),
            patch(
                "miramedia.imports.followup.run_post_import_completion",
                new=AsyncMock(),
            ),
        ):
            result = await service.resolve_manual_scan(body, claim_token="token-a")
        assert result.ok

    asyncio.run(_run())
    assert events.index("release") < events.index("import")
    repository.get_scan_cache_entry.assert_awaited_once_with(str(target))
    repository.complete_manual_scan_import.assert_awaited_once()


def test_resolve_scan_releases_before_movie_import_io(
    library_roots: tuple[Path, Path],
) -> None:
    _, movie_root = library_roots
    target = movie_root / "movie"
    target.mkdir()
    media_id = uuid.uuid4()
    movie = types.SimpleNamespace(id=media_id, name="Movie")
    movie_service = MagicMock()
    movie_service.get_movie_by_id = AsyncMock(return_value=movie)
    movie_service.import_movie_from_directory = AsyncMock(return_value=True)
    service, repository, _, _ = _resolve_service(
        directory=str(target),
        media_type=MediaType.movie,
        movie_service=movie_service,
    )
    body = ResolveRequest.model_validate(
        _scan_body(str(target), media_type=MediaType.movie, media_id=media_id)
    )
    events: list[str] = []

    async def _release(_db: object) -> None:
        events.append("release")

    async def _import(**_kwargs: object) -> bool:
        events.append("import")
        return True

    movie_service.import_movie_from_directory.side_effect = _import

    async def _run() -> None:
        with (
            patch(_RELEASE_PATCH, side_effect=_release),
            patch(
                "miramedia.imports.followup.run_post_import_completion",
                new=AsyncMock(),
            ),
        ):
            result = await service.resolve_manual_scan(body, claim_token="token-a")
        assert result.ok

    asyncio.run(_run())
    assert events.index("release") < events.index("import")
    repository.complete_manual_scan_import.assert_awaited_once()


def test_resolve_scan_terminal_cas_after_import_io(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    media_id = uuid.uuid4()
    show = types.SimpleNamespace(id=media_id, name="Show")
    show_service = MagicMock()
    show_service.get_show_by_id = AsyncMock(return_value=show)
    show_service.import_show_from_directory = AsyncMock(return_value=True)
    service, repository, _, _ = _resolve_service(
        directory=str(target),
        media_type=MediaType.show,
        show_service=show_service,
    )
    body = ResolveRequest.model_validate(_scan_body(str(target), media_id=media_id))
    events: list[str] = []

    async def _release(_db: object) -> None:
        events.append("release")

    async def _import(**_kwargs: object) -> bool:
        events.append("import")
        return True

    async def _complete(*_args: object, **_kwargs: object) -> bool:
        events.append("terminal_cas")
        return True

    show_service.import_show_from_directory.side_effect = _import
    repository.complete_manual_scan_import.side_effect = _complete

    async def _run() -> None:
        with (
            patch(_RELEASE_PATCH, side_effect=_release),
            patch(
                "miramedia.imports.followup.run_post_import_completion",
                new=AsyncMock(),
            ),
        ):
            await service.resolve_manual_scan(body, claim_token="token-a")

    asyncio.run(_run())
    assert events == ["release", "import", "terminal_cas"]


def test_resolve_scan_metadata_failure_writes_after_provider_io(
    library_roots: tuple[Path, Path],
) -> None:
    show_root, _ = library_roots
    target = show_root / "show"
    target.mkdir()
    show_service = MagicMock()
    service, repository, _, _ = _resolve_service(
        directory=str(target),
        media_type=MediaType.show,
        show_service=show_service,
    )
    body = ResolveRequest.model_validate(
        {
            "kind": "scan",
            "id": str(target),
            "media_type": "show",
            "external_id": "tt123",
            "metadata_provider": "tmdb",
        }
    )
    events: list[str] = []

    async def _release(_db: object) -> None:
        events.append("release")

    async def _add_show(**_kwargs: object) -> MagicMock:
        events.append("provider_io")
        msg = "provider miss"
        raise ValueError(msg)

    async def _fail(*_args: object, **_kwargs: object) -> bool:
        events.append("fail_write")
        return True

    show_service.add_show = AsyncMock(side_effect=_add_show)
    repository.fail_manual_scan_import.side_effect = _fail

    async def _run() -> None:
        with (
            patch(_RELEASE_PATCH, side_effect=_release),
            patch(
                "miramedia.metadata.dependencies.get_metadata_provider",
                return_value=MagicMock(),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await service.resolve_manual_scan(body, claim_token="token-a")
        assert exc_info.value.status_code == 422

    asyncio.run(_run())
    assert events == ["release", "provider_io", "fail_write"]


def _drive_get_session(exc: BaseException) -> tuple[MagicMock, MagicMock]:
    """Run get_session, throw ``exc`` into it, return (db, log) mocks."""
    import miramedia.database as database

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _Factory:
        def __call__(self) -> _Factory:
            return self

        async def __aenter__(self) -> MagicMock:
            return db

        async def __aexit__(self, *_args: object) -> bool:
            return False

    log = MagicMock()

    async def _run() -> None:
        with (
            patch.object(database, "SessionLocal", _Factory()),
            patch.object(database, "log", log),
        ):
            gen = database.get_session()
            await anext(gen)
            with pytest.raises(type(exc)):
                await gen.athrow(exc)

    asyncio.run(_run())
    return db, log


def test_get_session_does_not_log_request_validation_error() -> None:
    """A 422 body-validation failure is client error, not an unhandled crash."""
    from fastapi.exceptions import RequestValidationError

    db, log = _drive_get_session(RequestValidationError([]))

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
    log.exception.assert_not_called()


def test_get_session_logs_unexpected_error() -> None:
    db, log = _drive_get_session(RuntimeError("boom"))

    db.rollback.assert_awaited_once()
    log.exception.assert_called_once()

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from miramedia.auth.runtime import (
    OIDC_CONFIG_INVALID_DETAIL,
    AuthRuntimeActivationError,
    AuthRuntimeGeneration,
    prepare_auth_runtime_for_overrides,
)
from miramedia.auth.users import SuperuserDep, current_superuser
from miramedia.config import MiraMediaConfig
from miramedia.movies.cleanup import cleanup_stale_movie_preferences
from miramedia.settings.dependencies import settings_repository_dep
from miramedia.settings.integration_tests import HANDLERS as TEST_HANDLERS
from miramedia.settings.integration_tests import IntegrationTestResult
from miramedia.settings.mutation import (
    SETTINGS_MUTATION_FAILED_DETAIL,
    SettingsMutationError,
    SettingsMutationSupersededError,
    execute_settings_mutation,
)
from miramedia.settings.schemas import SystemSettingsRead, SystemSettingsUpdate
from miramedia.settings.service import (
    compute_clear_override_path,
    compute_mutation_overrides,
    get_effective_config,
    get_settings_schema,
    get_toml_defaults,
    strip_none,
)
from miramedia.settings.validation import (
    SettingsValidationError,
    reject_restart_only_clear_path,
    reject_restart_only_incoming,
    sanitize_export_overrides,
    validate_incoming_settings_update,
)
from miramedia.shows.cleanup import cleanup_stale_show_preferences

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def _stage_auth_runtime(
    merged_overrides: dict,
) -> AuthRuntimeGeneration:
    try:
        return await prepare_auth_runtime_for_overrides(merged_overrides)
    except AuthRuntimeActivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or OIDC_CONFIG_INVALID_DETAIL,
        ) from exc


async def _commit_settings_mutation(
    *,
    repo: settings_repository_dep,
    prepare: Callable[[], Awaitable[tuple[dict, dict, int]]],
) -> dict:
    async def _fetch_current() -> tuple[dict, int]:
        return await repo.get_overrides_with_revision()

    try:
        return await execute_settings_mutation(
            prepare=prepare,
            persist_overrides_cas=repo.save_overrides_cas,
            fetch_current=_fetch_current,
            db_session=repo.db,
            stage_auth_runtime=_stage_auth_runtime,
        )
    except SettingsMutationSupersededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SettingsMutationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=SETTINGS_MUTATION_FAILED_DETAIL,
        ) from exc


router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(current_superuser)],
)


_TEST_RATE_LIMIT_WINDOW_SECONDS = 60
_TEST_RATE_LIMIT_MAX_REQUESTS = 5
_test_call_log: dict[str, deque[float]] = defaultdict(deque)
_test_call_log_lock = threading.Lock()


async def _cleanup_stale_media_preferences(db: AsyncSession) -> None:
    """Null out per-show / per-movie quality/codec overrides that no longer
    reference an enabled option after a settings change. Called after every
    settings mutation; cheap when no rows have overrides.
    """
    try:
        config = MiraMediaConfig()
        await cleanup_stale_show_preferences(db, config)
        await cleanup_stale_movie_preferences(db, config)
    except Exception:
        log.exception(
            "Failed to clean up stale media preferences after settings change"
        )


def _rate_limit_test(user_key: str) -> None:
    """In-memory sliding-window rate limit for the integration test endpoints.

    Caps each superuser at ``_TEST_RATE_LIMIT_MAX_REQUESTS`` calls per
    ``_TEST_RATE_LIMIT_WINDOW_SECONDS`` seconds. Raises HTTP 429 when exceeded.
    """
    now = time.monotonic()
    with _test_call_log_lock:
        bucket = _test_call_log[user_key]
        cutoff = now - _TEST_RATE_LIMIT_WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _TEST_RATE_LIMIT_MAX_REQUESTS:
            retry_in = int(_TEST_RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0]))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many test requests. Try again in {max(1, retry_in)}s.",
                headers={"Retry-After": str(max(1, retry_in))},
            )
        bucket.append(now)


_ALLOWED_SECTIONS = {
    "misc",
    "auth",
    "notifications",
    "torrents",
    "indexers",
    "metadata",
    "requests",
    "subtitles",
    "updates",
    "cloudflare",
    "imports",
}


class SettingsSchemaEntry(BaseModel):
    """One leaf field in the flat settings search index."""

    path: list[str]
    section: str
    key: str
    label: str
    description: str
    type: str


@router.get("/settings/schema")
def get_settings_schema_endpoint() -> list[SettingsSchemaEntry]:
    """Flat searchable index of every settings leaf field (label, description, type, path)."""
    return [SettingsSchemaEntry(**entry) for entry in get_settings_schema()]


@router.get("/settings")
async def get_system_settings(
    repo: settings_repository_dep,
) -> SystemSettingsRead:
    """Get the effective system configuration (TOML defaults + DB overrides)."""
    overrides = await repo.get_overrides()
    effective = get_effective_config(overrides)
    return SystemSettingsRead(
        **effective, overrides=overrides, defaults=get_toml_defaults()
    )


@router.put("/settings")
async def update_system_settings(
    data: SystemSettingsUpdate,
    repo: settings_repository_dep,
) -> SystemSettingsRead:
    """Update system settings. Only provided fields are saved as overrides.

    Overrides are merged with existing overrides (not replaced) and applied
    to the in-memory config singleton so changes take effect immediately.
    Interval-driven scheduler tasks are also re-synced on save.
    """
    new_overrides = strip_none(data.model_dump(mode="json"))
    try:
        incoming_patch = validate_incoming_settings_update(new_overrides)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    async def _prepare() -> tuple[dict, dict, int]:
        prior_overrides, revision = await repo.get_overrides_with_revision()
        return (
            compute_mutation_overrides(prior_overrides, incoming_patch),
            prior_overrides,
            revision,
        )

    merged_overrides = await _commit_settings_mutation(
        repo=repo,
        prepare=_prepare,
    )

    # Clear per-show/movie overrides that reference now-disabled options
    await _cleanup_stale_media_preferences(repo.db)

    # Re-sync interval-driven taskiq schedules so cron changes take effect now
    try:
        from miramedia.scheduler import refresh_dynamic_schedules

        await refresh_dynamic_schedules()
    except Exception:
        log.exception("Failed to refresh dynamic schedules after settings update")

    effective = get_effective_config(merged_overrides)
    return SystemSettingsRead(**effective, overrides=merged_overrides)


@router.delete("/settings", status_code=204)
async def reset_system_settings(
    repo: settings_repository_dep,
) -> None:
    """Reset all system settings to TOML defaults (removes all DB overrides)."""

    async def _prepare() -> tuple[dict, dict, int]:
        prior_overrides, revision = await repo.get_overrides_with_revision()
        return {}, prior_overrides, revision

    await _commit_settings_mutation(
        repo=repo,
        prepare=_prepare,
    )

    await _cleanup_stale_media_preferences(repo.db)

    try:
        from miramedia.scheduler import refresh_dynamic_schedules

        await refresh_dynamic_schedules()
    except Exception:
        log.exception("Failed to refresh dynamic schedules after settings reset")


class SettingsExport(BaseModel):
    """Stable snapshot format for backup/restore of settings overrides.

    ``schema_version`` lets us evolve the file format without breaking older clients.
    """

    schema_version: int = 1
    exported_at: datetime
    overrides: dict


class SettingsImportRequest(BaseModel):
    overrides: dict
    mode: Literal["replace", "merge"] = "merge"


@router.get("/settings/export")
async def export_settings(repo: settings_repository_dep) -> SettingsExport:
    """Download every DB override as JSON for backup or transfer."""
    return SettingsExport(
        exported_at=datetime.now(UTC),
        overrides=sanitize_export_overrides(await repo.get_overrides()),
    )


@router.post("/settings/import")
async def import_settings(
    body: SettingsImportRequest,
    repo: settings_repository_dep,
) -> SystemSettingsRead:
    """Restore overrides from a previously exported snapshot.

    ``mode='replace'`` overwrites the entire override blob; ``'merge'`` deep-merges into
    the existing overrides (incoming values win on conflicts). Section names are validated
    against the known set so a malformed file can't poison the singleton.
    """
    incoming = body.overrides or {}
    try:
        reject_restart_only_incoming(incoming)
    except SettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    for key in incoming:
        if key not in _ALLOWED_SECTIONS:
            raise HTTPException(
                status_code=400, detail=f"Unknown section in import: {key}"
            )

    if body.mode == "replace":
        incoming_merged = incoming
    else:
        incoming_merged = None

    async def _prepare() -> tuple[dict, dict, int]:
        existing, revision = await repo.get_overrides_with_revision()
        if incoming_merged is not None:
            merged = compute_mutation_overrides({}, incoming_merged)
        else:
            merged = compute_mutation_overrides(existing, incoming)
        return merged, existing, revision

    merged = await _commit_settings_mutation(
        repo=repo,
        prepare=_prepare,
    )

    await _cleanup_stale_media_preferences(repo.db)

    try:
        from miramedia.scheduler import refresh_dynamic_schedules

        await refresh_dynamic_schedules()
    except Exception:
        log.exception("Failed to refresh dynamic schedules after settings import")

    effective = get_effective_config(merged)
    return SystemSettingsRead(**effective, overrides=merged)


class ClearOverridePathRequest(BaseModel):
    path: list[str] = Field(min_length=1)


@router.post("/settings/override/clear")
async def clear_override_path(
    body: ClearOverridePathRequest,
    repo: settings_repository_dep,
) -> SystemSettingsRead:
    """Remove a single override at a dotted path; revert that field to its TOML default in-memory."""
    if body.path[0] not in _ALLOWED_SECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section: {body.path[0]}",
        )
    try:
        reject_restart_only_clear_path(body.path)
    except SettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    async def _prepare() -> tuple[dict, dict, int]:
        prior, revision = await repo.get_overrides_with_revision()
        return compute_clear_override_path(prior, body.path), prior, revision

    updated_overrides = await _commit_settings_mutation(
        repo=repo,
        prepare=_prepare,
    )

    await _cleanup_stale_media_preferences(repo.db)

    try:
        from miramedia.scheduler import refresh_dynamic_schedules

        await refresh_dynamic_schedules()
    except Exception:
        log.exception("Failed to refresh dynamic schedules after override clear")

    effective = get_effective_config(updated_overrides)
    return SystemSettingsRead(**effective, overrides=updated_overrides)


class IntegrationTestRequest(BaseModel):
    """Live config snippet for the integration being tested.

    The shape varies per integration — pass the same field names the settings UI binds to,
    e.g. ``{"host": "localhost", "port": 8080, "username": "admin", "password": "..."}``
    for qBittorrent. Tests do not persist anything.
    """

    config: dict = Field(default_factory=dict)


@router.post("/settings/integrations/{integration}/test")
def test_integration(
    integration: str,
    body: IntegrationTestRequest,
    user: SuperuserDep,
) -> IntegrationTestResult:
    """Run a connection/auth test for the named integration without persisting changes.

    Rate-limited per superuser to avoid abuse against third-party APIs.
    """
    handler = TEST_HANDLERS.get(integration)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown integration: {integration}. "
            f"Known: {', '.join(sorted(TEST_HANDLERS))}",
        )
    _rate_limit_test(str(user.id))
    try:
        return handler(body.config)
    except Exception:
        log.exception("Integration test handler crashed for %s", integration)
        return IntegrationTestResult(
            ok=False, message="Handler crashed; see server logs for details"
        )

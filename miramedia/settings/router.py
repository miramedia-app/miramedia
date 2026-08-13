from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from miramedia.auth.runtime import (
    OIDC_CONFIG_INVALID_DETAIL,
    AuthRuntimeActivationError,
    AuthRuntimeGeneration,
    prepare_auth_runtime_for_overrides,
)
from miramedia.auth.users import SuperuserDep, current_superuser
from miramedia.config import MiraMediaConfig
from miramedia.movies.cleanup import cleanup_stale_movie_preferences
from miramedia.rate_limit import SlidingWindowLimiter
from miramedia.settings.dependencies import settings_repository_dep
from miramedia.settings.integration_tests import (
    HANDLERS,
    INTEGRATION_EFFECTIVE_PATHS,
    IntegrationTestResult,
    _format_config_errors,
)
from miramedia.settings.mutation import (
    SETTINGS_MUTATION_FAILED_DETAIL,
    SETTINGS_MUTATION_POSTCOMMIT_DETAIL,
    SETTINGS_MUTATION_ROLLBACK_INCOMPLETE_DETAIL,
    SETTINGS_MUTATION_SUPERSEDED_DETAIL,
    SettingsMutationError,
    SettingsMutationSupersededError,
    execute_settings_mutation,
)
from miramedia.settings.schemas import SystemSettingsRead, SystemSettingsUpdate
from miramedia.settings.service import (
    SETTINGS_SECTIONS,
    compute_clear_override_path,
    compute_mutation_overrides,
    get_effective_config,
    get_settings_schema,
    get_toml_defaults,
)
from miramedia.settings.validation import (
    SettingsValidationError,
    carry_forward_secrets,
    mask_secret_values,
    masked_credential_with_changed_target,
    reject_restart_only_clear_path,
    reject_restart_only_incoming,
    resolve_masked_config,
    sanitize_export_overrides,
    strip_masked_values,
    validate_incoming_settings_update,
)
from miramedia.shows.cleanup import cleanup_stale_show_preferences

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_OPERATOR_SAFE_MUTATION_DETAILS = frozenset(
    {
        SETTINGS_MUTATION_FAILED_DETAIL,
        SETTINGS_MUTATION_POSTCOMMIT_DETAIL,
        SETTINGS_MUTATION_ROLLBACK_INCOMPLETE_DETAIL,
        SETTINGS_MUTATION_SUPERSEDED_DETAIL,
    }
)


def _operator_safe_mutation_detail(exc: SettingsMutationError) -> str:
    message = str(exc)
    if message in _OPERATOR_SAFE_MUTATION_DETAILS:
        return message
    return SETTINGS_MUTATION_FAILED_DETAIL


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
    except SettingsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except SettingsMutationSupersededError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SettingsMutationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_operator_safe_mutation_detail(exc),
        ) from exc


router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(current_superuser)],
)


_TEST_RATE_LIMIT_WINDOW_SECONDS = 60
_TEST_RATE_LIMIT_MAX_REQUESTS = 5
_test_rate_limiter = SlidingWindowLimiter(
    max_requests=_TEST_RATE_LIMIT_MAX_REQUESTS,
    window_seconds=_TEST_RATE_LIMIT_WINDOW_SECONDS,
    detail_template="Too many test requests. Try again in {retry_in}s.",
)


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
    _test_rate_limiter.check(user_key)


_ALLOWED_SECTIONS = frozenset(SETTINGS_SECTIONS)


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


def _build_settings_read(
    *,
    overrides: dict,
    defaults: dict | None = None,
) -> SystemSettingsRead:
    effective = mask_secret_values(get_effective_config(overrides))
    masked_overrides = mask_secret_values(overrides)
    masked_defaults = mask_secret_values(defaults) if defaults is not None else None
    return SystemSettingsRead(
        **effective,
        overrides=masked_overrides,
        defaults=masked_defaults,
    )


@router.get("/settings")
async def get_system_settings(
    repo: settings_repository_dep,
) -> SystemSettingsRead:
    """Get the effective system configuration (TOML defaults + DB overrides)."""
    overrides = await repo.get_overrides()
    return _build_settings_read(overrides=overrides, defaults=get_toml_defaults())


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
    try:
        raw_patch = data.model_dump(mode="json", exclude_unset=True)
        incoming_patch = validate_incoming_settings_update(
            strip_masked_values(raw_patch)
        )
    except ValidationError as exc:
        log.exception("Settings update validation failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid settings payload",
        ) from exc
    except Exception as exc:
        log.exception("Settings update validation failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid settings payload",
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

    return _build_settings_read(overrides=merged_overrides)


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
    incoming = strip_masked_values(body.overrides or {})
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
            merged = carry_forward_secrets(
                existing,
                compute_mutation_overrides({}, incoming_merged),
            )
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

    return _build_settings_read(overrides=merged)


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

    return _build_settings_read(overrides=updated_overrides)


class IntegrationTestRequest(BaseModel):
    """Live config snippet for the integration being tested.

    The shape varies per integration — pass the same field names the settings UI binds to,
    e.g. ``{"host": "localhost", "port": 8080, "username": "admin", "password": "..."}``
    for qBittorrent. Tests do not persist anything.
    """

    config: dict = Field(default_factory=dict)


def _integration_effective_section(integration: str) -> dict:
    """Return the live effective config subsection for an integration test."""
    path = INTEGRATION_EFFECTIVE_PATHS.get(integration)
    if not path:
        return {}
    node: object = MiraMediaConfig()
    for key in path:
        node = getattr(node, key)
    if isinstance(node, BaseModel):
        return node.model_dump(mode="json")
    return {}


@router.post("/settings/integrations/{integration}/test")
def test_integration(
    integration: str,
    body: IntegrationTestRequest,
    user: SuperuserDep,
) -> IntegrationTestResult:
    """Run a connection/auth test for the named integration without persisting changes.

    Rate-limited per superuser to avoid abuse against third-party APIs.
    """
    entry = HANDLERS.get(integration)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown integration: {integration}. "
            f"Known: {', '.join(sorted(HANDLERS))}",
        )
    _rate_limit_test(str(user.id))
    model_cls, handler = entry
    section_path = INTEGRATION_EFFECTIVE_PATHS.get(integration, ())
    effective_section = _integration_effective_section(integration)
    if masked_credential_with_changed_target(
        body.config, effective_section, section_path
    ):
        return IntegrationTestResult(
            ok=False,
            message=("Connection target changed — re-enter the credential to test it."),
        )
    resolved_config = resolve_masked_config(
        body.config, effective_section, section_path
    )
    try:
        cfg = model_cls.model_validate(resolved_config)
    except ValidationError as exc:
        return IntegrationTestResult(ok=False, message=_format_config_errors(exc))
    try:
        return handler(cfg)
    except Exception:
        log.exception("Integration test handler crashed for %s", integration)
        return IntegrationTestResult(
            ok=False, message="Handler crashed; see server logs for details"
        )

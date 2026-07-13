"""Transactional settings mutations with snapshot rollback.

State machine (per worker):
1. Under async lock: single-row read (overrides + revision), capture snapshot.
2. Release DB session, release lock: Pydantic validate + OIDC discovery (no DB txn).
3. Under async lock: CAS persist (always with expected_revision) -> apply/swap -> publish.
4. On post-CAS apply failure: DB CAS restore first; on success apply snapshot + publish
   rollback revision; on CAS conflict reload current committed revision from DB.

Process-local ``_mutation_epoch`` guards same-worker interleaving; DB revision CAS
guards cross-worker writers. Never hold a DB transaction across OIDC network I/O.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    auth_runtime_store,
    prepare_auth_runtime_for_overrides,
)
from miramedia.settings.reload import (
    get_local_committed_revision,
    publish_settings_revision_changed,
    reconcile_settings_revision_from_db,
    reset_settings_reload_state_for_tests,
    set_local_committed_revision,
)
from miramedia.settings.repository import SettingsRevisionConflictError
from miramedia.settings.service import apply_live_config_from_overrides
from miramedia.settings.validation import sanitize_persisted_overrides

log = logging.getLogger(__name__)

_settings_mutation_lock = asyncio.Lock()
_mutation_epoch = 0

SETTINGS_MUTATION_FAILED_DETAIL = "Settings mutation failed"
SETTINGS_MUTATION_ROLLBACK_INCOMPLETE_DETAIL = (
    "Settings mutation failed and rollback was incomplete"
)
SETTINGS_MUTATION_SUPERSEDED_DETAIL = "Settings revision conflict; retry the mutation"


class SettingsMutationError(Exception):
    """Settings mutation failed after staging; state was rolled back."""


class SettingsMutationSupersededError(SettingsMutationError):
    """Another settings mutation completed while this one was staging."""


@dataclass(frozen=True, slots=True)
class SettingsMutationSnapshot:
    overrides: dict
    revision: int
    runtime_generation: AuthRuntimeGeneration
    epoch: int


async def capture_mutation_snapshot(
    overrides: dict,
    *,
    revision: int,
    epoch: int,
) -> SettingsMutationSnapshot:
    generation = auth_runtime_store.get_active()
    return SettingsMutationSnapshot(
        overrides=copy.deepcopy(overrides),
        revision=revision,
        runtime_generation=generation,
        epoch=epoch,
    )


def _apply_live_mutation_critical_section(
    merged_overrides: dict,
    prospective: AuthRuntimeGeneration,
) -> None:
    """Apply live config and activate runtime without awaiting."""
    apply_live_config_from_overrides(merged_overrides)
    from miramedia.auth.runtime import commit_auth_runtime_generation

    commit_auth_runtime_generation(prospective)


async def rollback_mutation_snapshot(
    snapshot: SettingsMutationSnapshot,
    *,
    restore_overrides_cas: Callable[[dict, int], Awaitable[tuple[dict, int]]],
    committed_revision: int,
    fetch_current: Callable[[], Awaitable[tuple[dict, int]]],
) -> None:
    """DB-authoritative rollback: CAS restore first, then in-memory apply."""
    if get_local_committed_revision() > committed_revision:
        await reconcile_settings_revision_from_db(fetch_current)
        return
    if (
        auth_runtime_store.get_active().generation_id
        > snapshot.runtime_generation.generation_id
    ):
        log.warning(
            "Skipping settings rollback: runtime advanced past snapshot revision %s",
            snapshot.revision,
        )
        await reconcile_settings_revision_from_db(fetch_current)
        return

    try:
        restored_overrides, rollback_revision = await restore_overrides_cas(
            snapshot.overrides,
            committed_revision,
        )
    except SettingsRevisionConflictError:
        log.warning(
            "Settings DB rollback conflict at revision %s; reconciling from DB",
            committed_revision,
        )
        await reconcile_settings_revision_from_db(fetch_current)
        return

    apply_live_config_from_overrides(restored_overrides)
    auth_runtime_store.restore(snapshot.runtime_generation)
    from miramedia.auth.users import apply_mutable_transport_settings

    apply_mutable_transport_settings()
    set_local_committed_revision(rollback_revision)
    publish_settings_revision_changed(rollback_revision)


async def execute_settings_mutation(
    *,
    prepare: Callable[[], Awaitable[tuple[dict, dict, int]]],
    persist_overrides_cas: Callable[[dict, int], Awaitable[tuple[dict, int]]],
    fetch_current: Callable[[], Awaitable[tuple[dict, int]]],
    db_session: AsyncSession | None = None,
    stage_auth_runtime: Callable[
        [dict], Awaitable[AuthRuntimeGeneration]
    ] = prepare_auth_runtime_for_overrides,
) -> dict:
    """Read/merge under lock, stage OIDC off-lock, then CAS persist/apply/swap under lock."""
    global _mutation_epoch

    async with _settings_mutation_lock:
        merged_overrides, prior_overrides, expected_revision = await prepare()
        sanitized = sanitize_persisted_overrides(merged_overrides)
        start_epoch = _mutation_epoch
        snapshot = await capture_mutation_snapshot(
            prior_overrides,
            revision=expected_revision,
            epoch=start_epoch,
        )

    if db_session is not None:
        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(db_session)

    prospective = await stage_auth_runtime(sanitized)

    committed_revision: int | None = None
    async with _settings_mutation_lock:
        if _mutation_epoch != start_epoch:
            raise SettingsMutationSupersededError(SETTINGS_MUTATION_SUPERSEDED_DETAIL)
        try:
            _overrides, committed_revision = await persist_overrides_cas(
                sanitized,
                expected_revision,
            )
            _apply_live_mutation_critical_section(sanitized, prospective)
            _mutation_epoch += 1
            set_local_committed_revision(committed_revision)
            publish_settings_revision_changed(committed_revision)
        except SettingsRevisionConflictError as exc:
            raise SettingsMutationSupersededError(
                SETTINGS_MUTATION_SUPERSEDED_DETAIL
            ) from exc
        except SettingsMutationSupersededError:
            raise
        except Exception as exc:
            if committed_revision is not None:
                log.exception(
                    "Settings mutation failed; rolling back to prior snapshot"
                )
                try:
                    await rollback_mutation_snapshot(
                        snapshot,
                        restore_overrides_cas=persist_overrides_cas,
                        committed_revision=committed_revision,
                        fetch_current=fetch_current,
                    )
                except Exception:
                    log.exception("Settings mutation rollback failed")
                    raise SettingsMutationError(
                        SETTINGS_MUTATION_ROLLBACK_INCOMPLETE_DETAIL
                    ) from exc
            raise SettingsMutationError(SETTINGS_MUTATION_FAILED_DETAIL) from exc
    return sanitized


def reset_settings_mutation_state_for_tests() -> None:
    """Reset mutation epoch (tests only)."""
    global _mutation_epoch
    _mutation_epoch = 0
    reset_settings_reload_state_for_tests()

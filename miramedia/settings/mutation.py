"""Transactional settings mutations with snapshot rollback.

State machine (per worker):
1. Under coordinator lock: single-row read (overrides + revision).
2. Release DB session, release lock: validate + stage prospective and prior runtimes.
3. Under coordinator lock: CAS persist -> apply only if not superseded -> publish.
4. On post-CAS apply failure: keep coordinator held, DB CAS restore using staged
   prior overrides/runtime; only release on CAS conflict to cross-process N+1.

Never bind rollback runtime to the process-active generation; always use the runtime
staged from the exact DB-prior overrides read under the coordinator.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    prepare_auth_runtime_for_overrides,
)
from miramedia.settings.coordinator import get_settings_coordinator_lock
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
    prior_runtime: AuthRuntimeGeneration
    epoch: int


def _raise_superseded_mutation() -> None:
    raise SettingsMutationSupersededError(SETTINGS_MUTATION_SUPERSEDED_DETAIL)


def _apply_live_mutation_critical_section(
    merged_overrides: dict,
    prospective: AuthRuntimeGeneration,
) -> None:
    """Apply live config and activate runtime without awaiting."""
    apply_live_config_from_overrides(merged_overrides)
    from miramedia.auth.runtime import commit_auth_runtime_generation

    commit_auth_runtime_generation(prospective)


async def _restore_committed_mutation_snapshot(
    snapshot: SettingsMutationSnapshot,
    *,
    committed_revision: int,
    restore_overrides_cas: Callable[[dict, int], Awaitable[tuple[dict, int]]],
) -> int:
    """DB-authoritative restore for one committed revision (caller holds coordinator)."""
    restored_overrides, rollback_revision = await restore_overrides_cas(
        snapshot.overrides,
        committed_revision,
    )
    apply_live_config_from_overrides(restored_overrides)
    from miramedia.auth.runtime import commit_auth_runtime_generation

    commit_auth_runtime_generation(snapshot.prior_runtime)
    from miramedia.auth.users import apply_mutable_transport_settings

    apply_mutable_transport_settings()
    set_local_committed_revision(rollback_revision)
    publish_settings_revision_changed(rollback_revision)
    return rollback_revision


async def rollback_mutation_snapshot(
    snapshot: SettingsMutationSnapshot,
    *,
    restore_overrides_cas: Callable[[dict, int], Awaitable[tuple[dict, int]]],
    committed_revision: int,
    fetch_current: Callable[[], Awaitable[tuple[dict, int]]],
) -> None:
    """Restore a committed revision; reconcile outside lock only on CAS conflict."""
    needs_reconcile = False
    coordinator = get_settings_coordinator_lock()
    async with coordinator:
        try:
            await _restore_committed_mutation_snapshot(
                snapshot,
                committed_revision=committed_revision,
                restore_overrides_cas=restore_overrides_cas,
            )
        except SettingsRevisionConflictError:
            log.warning(
                "Settings DB rollback conflict at revision %s; reconciling from DB",
                committed_revision,
            )
            needs_reconcile = True

    if needs_reconcile:
        await reconcile_settings_revision_from_db(fetch_current)


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

    coordinator = get_settings_coordinator_lock()
    async with coordinator:
        merged_overrides, prior_overrides, expected_revision = await prepare()
        sanitized = sanitize_persisted_overrides(merged_overrides)
        prior_for_stage = sanitize_persisted_overrides(prior_overrides)
        start_epoch = _mutation_epoch

    if db_session is not None:
        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(db_session)

    prospective = await stage_auth_runtime(sanitized)
    prior_runtime = await stage_auth_runtime(prior_for_stage)
    snapshot = SettingsMutationSnapshot(
        overrides=copy.deepcopy(prior_overrides),
        revision=expected_revision,
        prior_runtime=prior_runtime,
        epoch=start_epoch,
    )

    needs_reconcile = False
    mutation_error: BaseException | None = None

    async with coordinator:
        if _mutation_epoch != start_epoch:
            raise SettingsMutationSupersededError(SETTINGS_MUTATION_SUPERSEDED_DETAIL)
        committed_revision: int | None = None
        try:
            _overrides, committed_revision = await persist_overrides_cas(
                sanitized,
                expected_revision,
            )
            if committed_revision <= get_local_committed_revision():
                _raise_superseded_mutation()
            try:
                _apply_live_mutation_critical_section(sanitized, prospective)
            except Exception as apply_exc:
                mutation_error = apply_exc
                try:
                    await _restore_committed_mutation_snapshot(
                        snapshot,
                        committed_revision=committed_revision,
                        restore_overrides_cas=persist_overrides_cas,
                    )
                except SettingsRevisionConflictError:
                    needs_reconcile = True
                except Exception:
                    log.exception("Settings mutation rollback failed")
                    raise SettingsMutationError(
                        SETTINGS_MUTATION_ROLLBACK_INCOMPLETE_DETAIL
                    ) from apply_exc
                else:
                    log.exception(
                        "Settings mutation failed; rolled back committed revision %s",
                        committed_revision,
                    )
                    raise SettingsMutationError(
                        SETTINGS_MUTATION_FAILED_DETAIL
                    ) from apply_exc
            else:
                _mutation_epoch += 1
                set_local_committed_revision(committed_revision)
                publish_settings_revision_changed(committed_revision)
        except SettingsRevisionConflictError as exc:
            raise SettingsMutationSupersededError(
                SETTINGS_MUTATION_SUPERSEDED_DETAIL
            ) from exc
        except SettingsMutationSupersededError:
            raise
        except SettingsMutationError:
            raise
        except Exception as exc:
            if committed_revision is None:
                raise SettingsMutationError(SETTINGS_MUTATION_FAILED_DETAIL) from exc
            mutation_error = exc

    if needs_reconcile:
        try:
            await reconcile_settings_revision_from_db(fetch_current)
        except Exception:
            log.exception("Settings mutation reconcile after rollback conflict failed")
            raise SettingsMutationError(
                SETTINGS_MUTATION_ROLLBACK_INCOMPLETE_DETAIL
            ) from mutation_error
        raise SettingsMutationError(SETTINGS_MUTATION_FAILED_DETAIL) from mutation_error

    return sanitized


def reset_settings_mutation_state_for_tests() -> None:
    """Reset mutation epoch (tests only)."""
    global _mutation_epoch
    _mutation_epoch = 0
    reset_settings_reload_state_for_tests()

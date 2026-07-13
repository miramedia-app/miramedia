"""Transactional settings mutations with snapshot rollback.

State machine (per worker):
1. Under async lock: read/merge overrides + DB revision, capture snapshot.
2. Off lock: validate via staging (Pydantic + OIDC discovery; no DB txn held).
3. Under async lock: CAS persist -> no-await apply/swap critical section -> publish revision.
4. On failure after CAS: compensate only if committed revision is still current.

Process-local ``_mutation_epoch`` guards same-worker interleaving; DB revision CAS
guards cross-worker writers. Never hold a DB transaction across OIDC network I/O.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    auth_runtime_store,
    prepare_auth_runtime_for_overrides,
)
from miramedia.auth.users import restore_mutable_transport_settings
from miramedia.settings.reload import (
    get_local_committed_revision,
    publish_settings_revision_changed,
    reset_settings_reload_state_for_tests,
    set_local_committed_revision,
)
from miramedia.settings.repository import SettingsRevisionConflictError
from miramedia.settings.service import apply_live_config_from_overrides
from miramedia.settings.validation import sanitize_persisted_overrides

log = logging.getLogger(__name__)

_settings_mutation_lock = asyncio.Lock()
_mutation_epoch = 0


class SettingsMutationError(Exception):
    """Settings mutation failed after staging; state was rolled back."""


class SettingsMutationSupersededError(SettingsMutationError):
    """Another settings mutation completed while this one was staging."""


@dataclass(frozen=True, slots=True)
class SettingsMutationSnapshot:
    overrides: dict
    revision: int
    runtime_generation: AuthRuntimeGeneration
    cookie_secure: bool
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
        cookie_secure=generation.cookie_secure,
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
    restore_overrides_cas: Callable[[dict, int | None], Awaitable[tuple[dict, int]]],
    committed_revision: int | None,
) -> None:
    if (
        auth_runtime_store.get_active().generation_id
        > snapshot.runtime_generation.generation_id
    ):
        log.warning(
            "Skipping settings rollback: runtime advanced past snapshot revision %s",
            snapshot.revision,
        )
        return
    if (
        committed_revision is not None
        and get_local_committed_revision() > committed_revision
    ):
        log.warning(
            "Skipping settings DB rollback: revision advanced past %s",
            committed_revision,
        )
        return
    apply_live_config_from_overrides(snapshot.overrides)
    auth_runtime_store.restore(snapshot.runtime_generation)
    restore_mutable_transport_settings(snapshot.cookie_secure)
    if committed_revision is None:
        return
    try:
        await restore_overrides_cas(snapshot.overrides, committed_revision)
    except SettingsRevisionConflictError:
        log.warning(
            "Skipping settings DB rollback: revision no longer matches %s",
            committed_revision,
        )
    except Exception as exc:
        msg = "Database rollback failed after restoring in-memory auth state"
        raise SettingsMutationError(msg) from exc


async def execute_settings_mutation(
    *,
    prepare: Callable[[], Awaitable[tuple[dict, dict, int]]],
    persist_overrides_cas: Callable[[dict, int | None], Awaitable[tuple[dict, int]]],
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

    prospective = await stage_auth_runtime(sanitized)

    committed_revision: int | None = None
    async with _settings_mutation_lock:
        if _mutation_epoch != start_epoch:
            msg = "Settings mutation superseded by a newer change"
            raise SettingsMutationSupersededError(msg)
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
            msg = "Settings revision conflict; retry the mutation"
            raise SettingsMutationSupersededError(msg) from exc
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
                    )
                except Exception:
                    log.exception("Settings mutation rollback failed")
                    msg = "Settings mutation failed and rollback was incomplete"
                    raise SettingsMutationError(msg) from exc
            msg = f"Settings mutation failed: {exc}"
            raise SettingsMutationError(msg) from exc
    return sanitized


def reset_settings_mutation_state_for_tests() -> None:
    """Reset mutation epoch (tests only)."""
    global _mutation_epoch
    _mutation_epoch = 0
    reset_settings_reload_state_for_tests()

"""Transactional settings mutations with snapshot rollback."""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    auth_runtime_store,
    commit_auth_runtime_generation,
)
from miramedia.auth.users import restore_mutable_transport_settings
from miramedia.settings.service import apply_live_config_from_overrides

log = logging.getLogger(__name__)


class SettingsMutationError(Exception):
    """Settings mutation failed after staging; state was rolled back."""


@dataclass(frozen=True, slots=True)
class SettingsMutationSnapshot:
    overrides: dict
    runtime_generation: AuthRuntimeGeneration
    cookie_secure: bool


async def capture_mutation_snapshot(
    overrides: dict,
) -> SettingsMutationSnapshot:
    from miramedia.auth.users import openid_cookie_transport

    return SettingsMutationSnapshot(
        overrides=copy.deepcopy(overrides),
        runtime_generation=auth_runtime_store.get_active(),
        cookie_secure=openid_cookie_transport.cookie_secure,
    )


async def rollback_mutation_snapshot(
    snapshot: SettingsMutationSnapshot,
    *,
    restore_overrides: Callable[[dict], Awaitable[object]],
) -> None:
    apply_live_config_from_overrides(snapshot.overrides)
    auth_runtime_store.restore(snapshot.runtime_generation)
    restore_mutable_transport_settings(snapshot.cookie_secure)
    try:
        await restore_overrides(snapshot.overrides)
    except Exception as exc:
        msg = "Database rollback failed after restoring in-memory auth state"
        raise SettingsMutationError(msg) from exc


async def commit_validated_settings_mutation(
    merged_overrides: dict,
    prospective: AuthRuntimeGeneration,
    *,
    persist_overrides: Callable[[dict], Awaitable[object]],
    prior_overrides: dict,
) -> None:
    """Persist, apply live config, and activate runtime atomically with rollback."""
    snapshot = await capture_mutation_snapshot(prior_overrides)
    try:
        await persist_overrides(merged_overrides)
        apply_live_config_from_overrides(merged_overrides)
        commit_auth_runtime_generation(prospective)
    except Exception as exc:
        log.exception("Settings mutation failed; rolling back to prior snapshot")
        try:
            await rollback_mutation_snapshot(
                snapshot,
                restore_overrides=persist_overrides,
            )
        except Exception:
            log.exception("Settings mutation rollback failed")
            msg = "Settings mutation failed and rollback was incomplete"
            raise SettingsMutationError(msg) from exc
        msg = f"Settings mutation failed: {exc}"
        raise SettingsMutationError(msg) from exc

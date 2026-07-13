"""Transactional settings mutations with snapshot rollback."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from miramedia.auth.runtime import (
    AuthRuntimeGeneration,
    auth_runtime_store,
    commit_auth_runtime_generation,
    prepare_auth_runtime_for_overrides,
)
from miramedia.auth.users import restore_mutable_transport_settings
from miramedia.settings.service import apply_live_config_from_overrides

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
    runtime_generation: AuthRuntimeGeneration
    cookie_secure: bool
    epoch: int


async def capture_mutation_snapshot(
    overrides: dict,
    *,
    epoch: int,
) -> SettingsMutationSnapshot:
    from miramedia.auth.users import openid_cookie_transport

    return SettingsMutationSnapshot(
        overrides=copy.deepcopy(overrides),
        runtime_generation=auth_runtime_store.get_active(),
        cookie_secure=openid_cookie_transport.cookie_secure,
        epoch=epoch,
    )


def _apply_live_mutation_critical_section(
    merged_overrides: dict,
    prospective: AuthRuntimeGeneration,
) -> None:
    """Apply live config and activate runtime without awaiting.

    Must run under ``_settings_mutation_lock`` with no awaits between
    ``apply_live_config_from_overrides`` and ``commit_auth_runtime_generation``.
    """
    apply_live_config_from_overrides(merged_overrides)
    commit_auth_runtime_generation(prospective)


async def rollback_mutation_snapshot(
    snapshot: SettingsMutationSnapshot,
    *,
    restore_overrides: Callable[[dict], Awaitable[object]],
) -> None:
    if (
        auth_runtime_store.get_active().generation_id
        > snapshot.runtime_generation.generation_id
    ):
        log.warning(
            "Skipping settings rollback: runtime advanced past snapshot epoch %s",
            snapshot.epoch,
        )
        return
    apply_live_config_from_overrides(snapshot.overrides)
    auth_runtime_store.restore(snapshot.runtime_generation)
    restore_mutable_transport_settings(snapshot.cookie_secure)
    try:
        await restore_overrides(snapshot.overrides)
    except Exception as exc:
        msg = "Database rollback failed after restoring in-memory auth state"
        raise SettingsMutationError(msg) from exc


async def execute_settings_mutation(
    *,
    prepare: Callable[[], Awaitable[tuple[dict, dict]]],
    persist_overrides: Callable[[dict], Awaitable[object]],
    stage_auth_runtime: Callable[
        [dict], Awaitable[AuthRuntimeGeneration]
    ] = prepare_auth_runtime_for_overrides,
) -> dict:
    """Read/merge under lock, stage OIDC off-lock, then persist/apply/swap under lock."""
    global _mutation_epoch

    async with _settings_mutation_lock:
        merged_overrides, prior_overrides = await prepare()
        start_epoch = _mutation_epoch
        snapshot = await capture_mutation_snapshot(prior_overrides, epoch=start_epoch)

    prospective = await stage_auth_runtime(merged_overrides)

    async with _settings_mutation_lock:
        if _mutation_epoch != start_epoch:
            msg = "Settings mutation superseded by a newer change"
            raise SettingsMutationSupersededError(msg)
        try:
            await persist_overrides(merged_overrides)
            _apply_live_mutation_critical_section(merged_overrides, prospective)
            _mutation_epoch += 1
        except SettingsMutationSupersededError:
            raise
        except Exception as exc:
            if _mutation_epoch == start_epoch:
                log.exception(
                    "Settings mutation failed; rolling back to prior snapshot"
                )
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
    return merged_overrides


def reset_settings_mutation_state_for_tests() -> None:
    """Reset mutation epoch (tests only)."""
    global _mutation_epoch
    _mutation_epoch = 0

"""Settings mutation session release and rollback interleaving tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from miramedia.auth.runtime import reset_auth_runtime_for_tests
from miramedia.config import MiraMediaConfig
from miramedia.settings.mutation import (
    SettingsMutationSnapshot,
    execute_settings_mutation,
    rollback_mutation_snapshot,
)
from miramedia.settings.reload import (
    get_local_committed_revision,
    set_local_committed_revision,
)
from miramedia.settings.repository import SettingsRevisionConflictError
from miramedia.settings.service import apply_live_config_from_overrides
from tests.fakes.repositories import FakeSettingsRepository


@pytest.fixture(autouse=True)
def _reset() -> None:
    from miramedia.settings.mutation import reset_settings_mutation_state_for_tests

    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    apply_live_config_from_overrides({})


def test_execute_mutation_releases_db_session_before_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[bool] = []
    staged: list[bool] = []

    async def _release(_db: object) -> None:
        released.append(True)

    async def _stage(_overrides: dict) -> Any:
        staged.append(True)
        from miramedia.auth.runtime import build_auth_runtime_generation

        live = MiraMediaConfig()
        return await build_auth_runtime_generation(live.auth, live.misc)

    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io",
        _release,
    )

    repo = FakeSettingsRepository()

    async def _prepare() -> tuple[dict, dict, int]:
        return {"misc": {"development": True}}, {}, 0

    async def _fetch() -> tuple[dict, int]:
        return await repo.get_overrides_with_revision()

    async def _run() -> None:
        await execute_settings_mutation(
            prepare=_prepare,
            persist_overrides_cas=repo.save_overrides_cas,
            fetch_current=_fetch,
            db_session=repo.db,
            stage_auth_runtime=_stage,
        )

    asyncio.run(_run())
    assert released == [True]
    assert staged == [True, True]


def test_rollback_db_first_on_cas_conflict_reconciles_newer_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.runtime import build_auth_runtime_generation

    async def _run() -> None:
        reset_auth_runtime_for_tests()
        live = MiraMediaConfig()
        generation = await build_auth_runtime_generation(live.auth, live.misc)
        snapshot = SettingsMutationSnapshot(
            overrides={"misc": {"development": False}},
            revision=0,
            prior_runtime=generation,
            epoch=0,
        )
        set_local_committed_revision(1)

        async def _save(_overrides: dict, expected_revision: int) -> tuple[dict, int]:
            raise SettingsRevisionConflictError(expected_revision, 2)

        newer = {"misc": {"development": True}}

        async def _fetch() -> tuple[dict, int]:
            return newer, 2

        async def _reconcile(fetch: Any) -> None:
            overrides, revision = await fetch()
            apply_live_config_from_overrides(overrides)
            set_local_committed_revision(revision)

        monkeypatch.setattr(
            "miramedia.settings.mutation.reconcile_settings_revision_from_db",
            _reconcile,
        )

        await rollback_mutation_snapshot(
            snapshot,
            restore_overrides_cas=_save,
            committed_revision=1,
            fetch_current=_fetch,
        )
        assert MiraMediaConfig().misc.development is True
        assert get_local_committed_revision() == 2

    asyncio.run(_run())


def test_rollback_publishes_compensation_revision_on_success() -> None:
    published: list[int] = []

    async def _run() -> None:
        from miramedia.auth.runtime import build_auth_runtime_generation
        from miramedia.settings.mutation import rollback_mutation_snapshot

        live = MiraMediaConfig()
        prior_runtime = await build_auth_runtime_generation(live.auth, live.misc)
        snapshot = SettingsMutationSnapshot(
            overrides={"misc": {"development": False}},
            revision=0,
            prior_runtime=prior_runtime,
            epoch=0,
        )
        set_local_committed_revision(1)

        async def _save(_overrides: dict, expected_revision: int) -> tuple[dict, int]:
            return _overrides, expected_revision + 1

        async def _fetch() -> tuple[dict, int]:
            return snapshot.overrides, 1

        import miramedia.settings.mutation as mutation_mod

        original_publish = mutation_mod.publish_settings_revision_changed

        def _capture(revision: int) -> None:
            published.append(revision)
            original_publish(revision)

        mutation_mod.publish_settings_revision_changed = _capture
        try:
            await rollback_mutation_snapshot(
                snapshot,
                restore_overrides_cas=_save,
                committed_revision=1,
                fetch_current=_fetch,
            )
        finally:
            mutation_mod.publish_settings_revision_changed = original_publish

        assert published == [2]

    asyncio.run(_run())


def test_mutation_skips_apply_when_local_revision_advanced() -> None:
    from miramedia.settings.mutation import SettingsMutationSupersededError

    repo = FakeSettingsRepository()

    async def _prepare() -> tuple[dict, dict, int]:
        return {"misc": {"development": True}}, {}, 0

    async def _fetch() -> tuple[dict, int]:
        return await repo.get_overrides_with_revision()

    async def _stage(_overrides: dict) -> Any:
        from miramedia.auth.runtime import build_auth_runtime_generation

        live = MiraMediaConfig()
        return await build_auth_runtime_generation(live.auth, live.misc)

    async def _persist(overrides: dict, expected_revision: int) -> tuple[dict, int]:
        return overrides, expected_revision + 1

    async def _run() -> None:
        set_local_committed_revision(2)
        with pytest.raises(SettingsMutationSupersededError):
            await execute_settings_mutation(
                prepare=_prepare,
                persist_overrides_cas=_persist,
                fetch_current=_fetch,
                stage_auth_runtime=_stage,
            )
        assert get_local_committed_revision() == 2

    asyncio.run(_run())


def test_reload_wins_n_plus_one_interleaving_over_stale_mutation_apply() -> None:
    from miramedia.settings.mutation import SettingsMutationSupersededError
    from miramedia.settings.reload import reload_committed_settings

    staging_started = asyncio.Event()
    staging_release = asyncio.Event()

    async def _slow_stage(_overrides: dict) -> Any:
        from miramedia.auth.runtime import build_auth_runtime_generation

        staging_started.set()
        await staging_release.wait()
        live = MiraMediaConfig()
        return await build_auth_runtime_generation(live.auth, live.misc)

    async def _run() -> None:
        repo = FakeSettingsRepository()

        async def _prepare() -> tuple[dict, dict, int]:
            return {"misc": {"development": False}}, {}, 0

        async def _fetch() -> tuple[dict, int]:
            return {"misc": {"development": True}}, 2

        mutation_task = asyncio.create_task(
            execute_settings_mutation(
                prepare=_prepare,
                persist_overrides_cas=repo.save_overrides_cas,
                fetch_current=_fetch,
                stage_auth_runtime=_slow_stage,
            )
        )
        await staging_started.wait()
        await reload_committed_settings(
            {"misc": {"development": True}},
            revision=2,
        )
        staging_release.set()
        with pytest.raises(SettingsMutationSupersededError):
            await mutation_task

        assert get_local_committed_revision() == 2
        assert MiraMediaConfig().misc.development is True

    asyncio.run(_run())


def test_rollback_reconcile_stages_outside_coordinator_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.runtime import build_auth_runtime_generation
    from miramedia.settings.coordinator import get_settings_coordinator_lock

    lock_held: list[bool] = []

    async def _stage_outside_lock(_overrides: dict) -> Any:
        lock_held.append(get_settings_coordinator_lock().locked())
        live = MiraMediaConfig()
        return await build_auth_runtime_generation(live.auth, live.misc)

    async def _reconcile(fetch: Any) -> None:
        from miramedia.settings.reload import reload_committed_settings

        overrides, revision = await fetch()
        await reload_committed_settings(overrides, revision=revision)

    monkeypatch.setattr(
        "miramedia.settings.mutation.reconcile_settings_revision_from_db",
        _reconcile,
    )
    monkeypatch.setattr(
        "miramedia.settings.reload.prepare_auth_runtime_for_overrides",
        _stage_outside_lock,
    )

    async def _run() -> None:
        live = MiraMediaConfig()
        generation = await build_auth_runtime_generation(live.auth, live.misc)
        snapshot = SettingsMutationSnapshot(
            overrides={"misc": {"development": False}},
            revision=0,
            prior_runtime=generation,
            epoch=0,
        )
        set_local_committed_revision(1)

        async def _save(_overrides: dict, expected_revision: int) -> tuple[dict, int]:
            raise SettingsRevisionConflictError(expected_revision, 2)

        async def _fetch() -> tuple[dict, int]:
            return {"misc": {"development": True}}, 2

        await rollback_mutation_snapshot(
            snapshot,
            restore_overrides_cas=_save,
            committed_revision=1,
            fetch_current=_fetch,
        )
        assert lock_held == [False]
        assert get_local_committed_revision() == 2

    asyncio.run(_run())


def test_apply_failure_rollback_holds_lock_against_same_revision_reload() -> None:
    from miramedia.settings.coordinator import get_settings_coordinator_lock
    from miramedia.settings.mutation import SettingsMutationError
    from miramedia.settings.reload import reload_committed_settings

    reload_entered = asyncio.Event()
    rollback_finished = asyncio.Event()

    original_apply = __import__(
        "miramedia.settings.mutation",
        fromlist=["_apply_live_mutation_critical_section"],
    )._apply_live_mutation_critical_section

    def _boom_apply(overrides: dict, prospective: Any) -> None:
        original_apply(overrides, prospective)
        msg = "apply failed"
        raise RuntimeError(msg)

    async def _run() -> None:
        repo = FakeSettingsRepository()
        coordinator = get_settings_coordinator_lock()

        async def _prepare() -> tuple[dict, dict, int]:
            return {"misc": {"development": True}}, {}, 0

        async def _fetch() -> tuple[dict, int]:
            return {"misc": {"development": True}}, 1

        async def _stage(_overrides: dict) -> Any:
            from miramedia.auth.runtime import build_auth_runtime_generation

            live = MiraMediaConfig()
            return await build_auth_runtime_generation(live.auth, live.misc)

        import miramedia.settings.mutation as mutation_mod

        mutation_mod._apply_live_mutation_critical_section = _boom_apply
        original_restore = mutation_mod._restore_committed_mutation_snapshot

        async def _slow_restore(*args: Any, **kwargs: Any) -> int:
            reload_entered.set()
            await rollback_finished.wait()
            return await original_restore(*args, **kwargs)

        mutation_mod._restore_committed_mutation_snapshot = _slow_restore
        try:
            mutation_task = asyncio.create_task(
                execute_settings_mutation(
                    prepare=_prepare,
                    persist_overrides_cas=repo.save_overrides_cas,
                    fetch_current=_fetch,
                    stage_auth_runtime=_stage,
                )
            )
            reload_task = asyncio.create_task(
                reload_committed_settings(
                    {"misc": {"development": False}},
                    revision=1,
                )
            )
            await reload_entered.wait()
            assert coordinator.locked()
            assert not reload_task.done()
            rollback_finished.set()
            with pytest.raises(SettingsMutationError):
                await mutation_task
            await reload_task
        finally:
            mutation_mod._apply_live_mutation_critical_section = original_apply
            mutation_mod._restore_committed_mutation_snapshot = original_restore

        assert get_local_committed_revision() == 2
        assert MiraMediaConfig().misc.development is False

    asyncio.run(_run())


def test_failed_mutation_rollback_restores_staged_prior_runtime_not_active_store() -> (
    None
):
    from miramedia.auth.runtime import (
        auth_runtime_store,
        build_auth_runtime_generation,
        commit_auth_runtime_generation,
    )
    from miramedia.settings.mutation import SettingsMutationError
    from miramedia.settings.service import build_isolated_config

    async def _stage_from_overrides(overrides: dict) -> Any:
        config = build_isolated_config(overrides)
        return await build_auth_runtime_generation(config.auth, config.misc)

    async def _run() -> None:
        prior_overrides = {
            "misc": {
                "development": True,
                "frontend_url": "http://prior-b.example/",
            }
        }
        repo = FakeSettingsRepository(overrides=prior_overrides, revision=2)

        stale_overrides = {
            "misc": {
                "development": False,
                "frontend_url": "http://stale-a.example/",
            }
        }
        apply_live_config_from_overrides(stale_overrides)
        stale_runtime = await _stage_from_overrides(stale_overrides)
        commit_auth_runtime_generation(stale_runtime)
        set_local_committed_revision(1)

        original_apply = __import__(
            "miramedia.settings.mutation",
            fromlist=["_apply_live_mutation_critical_section"],
        )._apply_live_mutation_critical_section

        def _boom_apply(overrides: dict, prospective: Any) -> None:
            original_apply(overrides, prospective)
            msg = "apply failed"
            raise RuntimeError(msg)

        import miramedia.settings.mutation as mutation_mod

        mutation_mod._apply_live_mutation_critical_section = _boom_apply
        try:

            async def _prepare() -> tuple[dict, dict, int]:
                prior, revision = await repo.get_overrides_with_revision()
                return (
                    {
                        "misc": {
                            "development": False,
                            "frontend_url": "http://prospective-c.example/",
                        }
                    },
                    prior,
                    revision,
                )

            async def _fetch() -> tuple[dict, int]:
                return await repo.get_overrides_with_revision()

            with pytest.raises(SettingsMutationError):
                await execute_settings_mutation(
                    prepare=_prepare,
                    persist_overrides_cas=repo.save_overrides_cas,
                    fetch_current=_fetch,
                    stage_auth_runtime=_stage_from_overrides,
                )
        finally:
            mutation_mod._apply_live_mutation_critical_section = original_apply

        assert MiraMediaConfig().misc.development is True
        assert str(MiraMediaConfig().misc.frontend_url) == "http://prior-b.example/"
        assert auth_runtime_store.get_active().frontend_url == "http://prior-b.example/"
        assert get_local_committed_revision() == 4

    asyncio.run(_run())

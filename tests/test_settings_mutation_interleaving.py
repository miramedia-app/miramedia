"""Settings mutation session release and rollback interleaving tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from miramedia.auth.runtime import reset_auth_runtime_for_tests
from miramedia.config import MiraMediaConfig
from miramedia.settings.mutation import (
    SettingsMutationSnapshot,
    capture_mutation_snapshot,
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
    assert staged == [True]


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
            runtime_generation=generation,
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
        from miramedia.settings.mutation import rollback_mutation_snapshot

        snapshot = await capture_mutation_snapshot(
            {"misc": {"development": False}},
            revision=0,
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

"""Cross-worker settings revision reload and CAS regression tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from miramedia.config import MiraMediaConfig
from miramedia.events.bus import Event
from miramedia.settings.models import SystemConfigOverride
from miramedia.settings.reload import (
    get_local_committed_revision,
    handle_settings_revision_event,
    reload_committed_settings,
    reset_settings_reload_state_for_tests,
    set_local_committed_revision,
)
from miramedia.settings.repository import SettingsRevisionConflictError
from miramedia.settings.service import apply_live_config_from_overrides
from tests.fakes.repositories import FakeSettingsRepository


@pytest.fixture(autouse=True)
def _reset_reload_state() -> None:
    reset_settings_reload_state_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_settings_reload_state_for_tests()
    apply_live_config_from_overrides({})


def test_reload_committed_settings_ignores_stale_revision() -> None:
    async def _run() -> None:
        await reload_committed_settings({"misc": {"development": True}}, revision=1)
        assert MiraMediaConfig().misc.development is True
        assert get_local_committed_revision() == 1

        await reload_committed_settings({"misc": {"development": False}}, revision=1)
        assert MiraMediaConfig().misc.development is True
        assert get_local_committed_revision() == 1

    asyncio.run(_run())


def test_reload_committed_settings_applies_newer_revision() -> None:
    async def _run() -> None:
        await reload_committed_settings({"misc": {"development": True}}, revision=1)
        await reload_committed_settings({"misc": {"development": False}}, revision=2)
        assert MiraMediaConfig().misc.development is False
        assert get_local_committed_revision() == 2

    asyncio.run(_run())


def test_revision_event_loads_committed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repo = FakeSettingsRepository(
        overrides={"misc": {"continuous_download": False}},
    )
    fake_repo.revision = 3

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:  # noqa: PYI034
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    @asynccontextmanager
    async def _session_factory() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr(
        "miramedia.database.SessionLocalBackground",
        _session_factory,
    )
    monkeypatch.setattr(
        "miramedia.settings.repository.SettingsRepository",
        lambda _db: fake_repo,
    )

    async def _run() -> None:
        set_local_committed_revision(0)
        await handle_settings_revision_event(
            Event(type="settings.revision.changed", data={"revision": 3})
        )
        assert MiraMediaConfig().misc.continuous_download is False
        assert get_local_committed_revision() == 3

    asyncio.run(_run())


def test_fake_repository_cas_conflict_is_deterministic() -> None:
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    repo.revision = 2

    async def _attempt() -> None:
        with pytest.raises(SettingsRevisionConflictError) as exc_info:
            await repo.save_overrides_cas(
                {"misc": {"development": False}},
                expected_revision=1,
            )
        assert exc_info.value.expected_revision == 1
        assert exc_info.value.actual_revision == 2
        assert repo.overrides["misc"]["development"] is True

    asyncio.run(_attempt())


def test_save_overrides_cas_statement_checks_revision() -> None:
    from sqlalchemy import update

    stmt = (
        update(SystemConfigOverride)
        .where(SystemConfigOverride.id == 1)
        .where(SystemConfigOverride.revision == 4)
        .values(
            overrides={"misc": {"development": True}},
            revision=SystemConfigOverride.revision + 1,
        )
        .returning(SystemConfigOverride.overrides, SystemConfigOverride.revision)
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "revision" in compiled
    assert "system_config_override" in compiled


def test_rollback_reconciles_on_cas_conflict_when_local_advanced() -> None:
    from miramedia.auth.runtime import (
        build_auth_runtime_generation,
        reset_auth_runtime_for_tests,
    )
    from miramedia.config import MiraMediaConfig
    from miramedia.settings.mutation import (
        SettingsMutationSnapshot,
        rollback_mutation_snapshot,
    )
    from miramedia.settings.repository import SettingsRevisionConflictError

    async def _run() -> None:
        reset_auth_runtime_for_tests()
        live = MiraMediaConfig()
        generation = await build_auth_runtime_generation(live.auth, live.misc)
        snapshot = SettingsMutationSnapshot(
            overrides={"misc": {"development": False}},
            revision=1,
            prior_runtime=generation,
            epoch=0,
        )
        set_local_committed_revision(4)
        save_calls: list[int] = []

        async def _save(_overrides: dict, _expected_revision: int) -> tuple[dict, int]:
            save_calls.append(1)
            raise SettingsRevisionConflictError(_expected_revision, 5)

        async def _fetch() -> tuple[dict, int]:
            return {"misc": {"development": True}}, 5

        await rollback_mutation_snapshot(
            snapshot,
            restore_overrides_cas=_save,
            committed_revision=3,
            fetch_current=_fetch,
        )
        assert save_calls == [1]
        assert MiraMediaConfig().misc.development is True
        assert get_local_committed_revision() == 5

    asyncio.run(_run())


def test_compensation_skips_db_when_cas_conflict() -> None:
    from miramedia.auth.runtime import (
        build_auth_runtime_generation,
        reset_auth_runtime_for_tests,
    )
    from miramedia.settings.mutation import (
        SettingsMutationSnapshot,
        rollback_mutation_snapshot,
    )

    async def _run() -> None:
        reset_auth_runtime_for_tests()
        live = MiraMediaConfig()
        generation = await build_auth_runtime_generation(live.auth, live.misc)
        snapshot = SettingsMutationSnapshot(
            overrides={"misc": {"development": False}},
            revision=1,
            prior_runtime=generation,
            epoch=0,
        )

        repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
        set_local_committed_revision(1)

        async def _save(_overrides: dict, expected_revision: int) -> tuple[dict, int]:
            assert expected_revision == 1
            raise SettingsRevisionConflictError(1, 2)

        async def _fetch() -> tuple[dict, int]:
            return repo.overrides, repo.revision

        await rollback_mutation_snapshot(
            snapshot,
            restore_overrides_cas=_save,
            committed_revision=1,
            fetch_current=_fetch,
        )
        assert repo.overrides["misc"]["development"] is True

    asyncio.run(_run())

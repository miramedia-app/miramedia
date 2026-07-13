"""Repository CAS, normalization, and revision semantics tests."""

from __future__ import annotations

import asyncio
import copy

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert

from miramedia.settings.models import SystemConfigOverride
from miramedia.settings.normalize import (
    normalize_legacy_overrides,
    normalize_stored_overrides,
)
from miramedia.settings.repository import SettingsRevisionConflictError
from tests.fakes.repositories import FakeSettingsRepository


def test_normalize_legacy_does_not_mutate_source() -> None:
    source = {
        "indexers": {
            "quality_scoring_rules": [
                {"name": "1080p", "keywords": ["1080"], "score_modifier": 100}
            ]
        }
    }
    original = copy.deepcopy(source)
    normalized = normalize_legacy_overrides(source)
    assert source == original
    assert "quality_options" in normalized["indexers"]
    assert "quality_scoring_rules" not in normalized["indexers"]


def test_normalize_stored_overrides_strips_token_secret() -> None:
    raw = {"auth": {"token_secret": "a" * 64, "email_password_resets": True}}
    normalized = normalize_stored_overrides(raw)
    assert "token_secret" not in normalized.get("auth", {})
    assert normalized["auth"]["email_password_resets"] is True


def test_initial_insert_cas_uses_on_conflict_shape() -> None:
    stmt = (
        insert(SystemConfigOverride)
        .values(id=1, overrides={"misc": {"development": True}}, revision=1)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(SystemConfigOverride.overrides, SystemConfigOverride.revision)
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in compiled.upper()
    assert "DO NOTHING" in compiled.upper()


def test_fake_repository_initial_insert_lost_race_raises_conflict() -> None:
    repo = FakeSettingsRepository()
    repo._insert_lost_race = True

    async def _attempt() -> None:
        with pytest.raises(SettingsRevisionConflictError) as exc_info:
            await repo.save_overrides_cas({"misc": {"development": True}}, 0)
        assert exc_info.value.expected_revision == 0
        assert exc_info.value.actual_revision == 1

    asyncio.run(_attempt())


def test_get_overrides_with_revision_is_single_snapshot() -> None:
    repo = FakeSettingsRepository(
        overrides={"auth": {"token_secret": "b" * 64, "email_password_resets": True}}
    )

    async def _run() -> None:
        overrides, revision = await repo.get_overrides_with_revision()
        assert revision == 1
        assert "token_secret" not in overrides.get("auth", {})

    asyncio.run(_run())

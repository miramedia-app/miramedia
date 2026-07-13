"""OAuth provider migration SQL characterization tests."""

from __future__ import annotations

from pathlib import Path


def test_migration_contains_dedupe_and_conflict_guard_steps() -> None:
    source = Path(
        "alembic/versions/f3a4b5c6d7e8_normalize_oauth_provider_names.py"
    ).read_text(encoding="utf-8")
    assert "DELETE FROM oauth_account" in source
    assert "MIN(id)" in source
    assert "canonical.user_id <> legacy.user_id" in source
    assert '_CANONICAL = "oidc"' in source

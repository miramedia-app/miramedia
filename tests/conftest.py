"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from tests.oauth_test_helpers import install_openid_client_factory


@pytest.fixture(autouse=True)
def _mock_oidc_openid_client_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    install_openid_client_factory(monkeypatch)


@pytest.fixture(autouse=True)
def _reset_settings_mutation_isolation() -> Generator[None]:
    from miramedia.auth.runtime import reset_auth_runtime_for_tests
    from miramedia.settings.mutation import reset_settings_mutation_state_for_tests
    from miramedia.settings.service import apply_live_config_from_overrides

    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()

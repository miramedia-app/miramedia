"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

from collections.abc import Callable, Generator

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


@pytest.fixture
def override_dependency() -> Generator[Callable[[Callable, object], None]]:
    """Override FastAPI dependencies with guaranteed restore.

    Usage:
        override_dependency(get_session, _stub_session)
        override_dependency(current_active_user, _active_user)
    Overrides accumulate for the test's duration; the prior mapping is
    restored afterwards even on failure.
    """
    from miramedia.main import app

    prior = dict(app.dependency_overrides)

    def _override(dependency: Callable, replacement: object) -> None:
        app.dependency_overrides[dependency] = replacement

    try:
        yield _override
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)

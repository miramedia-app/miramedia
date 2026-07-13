"""Shared test setup.

Environment must be set before any ``miramedia`` import: config and logging
initialise at import time (see ``miramedia/config.py:36`` and the Makefile
``openapi`` target, which sets ``MIRAMEDIA_LOG_FILE`` for the same reason).
"""

import os
from collections.abc import Generator

import pytest

os.environ.setdefault("MIRAMEDIA_LOG_FILE", "/dev/null")


@pytest.fixture(autouse=True)
def _reset_settings_coordination_state() -> Generator[None]:
    from miramedia.auth.runtime import reset_auth_runtime_for_tests
    from miramedia.settings.mutation import reset_settings_mutation_state_for_tests
    from miramedia.settings.reload import reset_settings_subscriber_for_tests
    from miramedia.settings.service import apply_live_config_from_overrides

    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    reset_settings_subscriber_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    reset_settings_subscriber_for_tests()
    apply_live_config_from_overrides({})

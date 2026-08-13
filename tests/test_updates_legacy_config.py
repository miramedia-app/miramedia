"""Legacy in-app apply config keys are accepted but ignored."""

from __future__ import annotations

import logging

import pytest

from miramedia.updates.config import UpdateConfig


def test_legacy_apply_settings_warn_and_strip(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        cfg = UpdateConfig.model_validate(
            {
                "allow_in_app_apply": True,
                "docker_socket_path": "/var/run/docker.sock",
                "container_name": "legacy",
                "repo": "org/repo",
            }
        )
    assert cfg.repo == "org/repo"
    assert not hasattr(cfg, "allow_in_app_apply")
    assert any("Legacy in-app apply settings" in r.message for r in caplog.records)

"""Import-order contract: settings service and validation must not cycle."""

from __future__ import annotations

import importlib
import sys


def _reload_settings_modules() -> None:
    for name in (
        "miramedia.settings.composition",
        "miramedia.settings.validation",
        "miramedia.settings.service",
    ):
        sys.modules.pop(name, None)


def test_service_then_validation_import_without_partial_init() -> None:
    _reload_settings_modules()
    service = importlib.import_module("miramedia.settings.service")
    validation = importlib.import_module("miramedia.settings.validation")
    assert service.SETTINGS_SECTIONS == validation.SETTINGS_SECTIONS


def test_validation_then_service_import_without_partial_init() -> None:
    _reload_settings_modules()
    validation = importlib.import_module("miramedia.settings.validation")
    service = importlib.import_module("miramedia.settings.service")
    assert service.SETTINGS_SECTIONS == validation.SETTINGS_SECTIONS

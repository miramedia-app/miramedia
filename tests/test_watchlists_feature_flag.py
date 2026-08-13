"""Tests for watchlists feature flag gates."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import HTTPException

from miramedia.config import MiraMediaConfig
from miramedia.watchlists.dependencies import (
    require_continue_watching_enabled,
    require_custom_lists_enabled,
    require_upcoming_enabled,
    require_watch_next_enabled,
    require_watchlists_enabled,
)


@pytest.fixture(autouse=True)
def _restore_watchlists_config() -> Generator[None]:
    cfg = MiraMediaConfig().watchlists
    native = cfg.native
    original = (
        cfg.auto_remove_watched,
        cfg.continue_watching,
        cfg.max_lists_per_user,
        cfg.max_items_per_list,
        native.enabled,
        native.custom_lists,
        native.watch_next,
        native.watch_next_include_specials,
        native.upcoming,
        native.upcoming_default_past_days,
        native.upcoming_default_future_days,
    )
    yield
    (
        cfg.auto_remove_watched,
        cfg.continue_watching,
        cfg.max_lists_per_user,
        cfg.max_items_per_list,
        native.enabled,
        native.custom_lists,
        native.watch_next,
        native.watch_next_include_specials,
        native.upcoming,
        native.upcoming_default_past_days,
        native.upcoming_default_future_days,
    ) = original


def test_require_watchlists_enabled_raises_503_when_disabled() -> None:
    MiraMediaConfig().watchlists.native.enabled = False

    with pytest.raises(HTTPException) as exc_info:
        require_watchlists_enabled()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Watchlists feature is disabled"


def test_require_watchlists_enabled_passes_when_enabled() -> None:
    MiraMediaConfig().watchlists.native.enabled = True

    require_watchlists_enabled()  # should not raise


def test_require_custom_lists_enabled_raises_when_flag_off() -> None:
    MiraMediaConfig().watchlists.native.enabled = True
    MiraMediaConfig().watchlists.native.custom_lists = False

    with pytest.raises(HTTPException) as exc_info:
        require_custom_lists_enabled()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Custom lists feature is disabled"


def test_require_watch_next_enabled_raises_when_flag_off() -> None:
    MiraMediaConfig().watchlists.native.enabled = True
    MiraMediaConfig().watchlists.native.watch_next = False

    with pytest.raises(HTTPException) as exc_info:
        require_watch_next_enabled()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Watch Next feature is disabled"


def test_require_watch_next_enabled_raises_when_master_off() -> None:
    MiraMediaConfig().watchlists.native.enabled = False
    MiraMediaConfig().watchlists.native.watch_next = True

    with pytest.raises(HTTPException) as exc_info:
        require_watch_next_enabled()

    assert exc_info.value.status_code == 503


def test_require_upcoming_enabled_raises_when_flag_off() -> None:
    MiraMediaConfig().watchlists.native.enabled = True
    MiraMediaConfig().watchlists.native.upcoming = False

    with pytest.raises(HTTPException) as exc_info:
        require_upcoming_enabled()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Upcoming feature is disabled"


def test_derived_flags_follow_master_and_subflags() -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.native.enabled = True
    cfg.native.custom_lists = True
    cfg.native.watch_next = True
    cfg.native.upcoming = False
    assert cfg.custom_lists_enabled is True
    assert cfg.watch_next_enabled is True
    assert cfg.upcoming_enabled is False

    cfg.native.enabled = False
    assert cfg.custom_lists_enabled is False
    assert cfg.watch_next_enabled is False
    assert cfg.upcoming_enabled is False


def test_require_continue_watching_enabled_raises_when_flag_off() -> None:
    MiraMediaConfig().watchlists.continue_watching = False

    with pytest.raises(HTTPException) as exc_info:
        require_continue_watching_enabled()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Continue Watching feature is disabled"


def test_require_continue_watching_enabled_passes_when_enabled() -> None:
    MiraMediaConfig().watchlists.continue_watching = True

    require_continue_watching_enabled()  # should not raise


def test_continue_watching_enabled_is_independent_of_native_master() -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.native.enabled = False
    cfg.continue_watching = True
    assert cfg.continue_watching_enabled is True

    cfg.continue_watching = False
    assert cfg.continue_watching_enabled is False

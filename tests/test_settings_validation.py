"""Settings override validation and restart-only field boundaries."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from miramedia.config import MiraMediaConfig
from miramedia.settings.validation import (
    RESTART_ONLY_OVERRIDE_PATHS,
    SettingsValidationError,
    build_merged_validated_config,
    reject_restart_only_incoming,
    sanitize_export_overrides,
    sanitize_persisted_overrides,
    strip_restart_only_overrides,
    validate_incoming_settings_update,
)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_incoming_settings_update({"misc": {"timezone": "Mars/Phobos"}})


def test_valid_iana_timezone_is_accepted() -> None:
    out = validate_incoming_settings_update({"misc": {"timezone": "America/New_York"}})
    assert out["misc"]["timezone"] == "America/New_York"


def test_blank_timezone_is_accepted_as_server_default() -> None:
    out = validate_incoming_settings_update({"misc": {"timezone": ""}})
    assert out["misc"]["timezone"] == ""


def test_string_false_is_not_coerced_to_true() -> None:
    with pytest.raises(SettingsValidationError):
        build_merged_validated_config({"auth": {"email_password_resets": "false"}})


def test_invalid_frontend_url_is_rejected() -> None:
    with pytest.raises(SettingsValidationError):
        build_merged_validated_config({"misc": {"frontend_url": "not-a-valid-url"}})


def test_unknown_top_level_override_field_is_rejected() -> None:
    with pytest.raises(SettingsValidationError):
        build_merged_validated_config({"misc": {"bogus_field": True}})


def test_valid_frontend_url_is_normalized() -> None:
    config = build_merged_validated_config(
        {"misc": {"frontend_url": "https://app.example.com"}}
    )
    assert str(config.misc.frontend_url).startswith("https://app.example.com")


def test_token_secret_stripped_from_persisted_overrides() -> None:
    overrides = {
        "auth": {
            "token_secret": "a" * 64,
            "email_password_resets": True,
        }
    }
    sanitized = strip_restart_only_overrides(overrides)
    assert "token_secret" not in sanitized.get("auth", {})


def test_token_secret_rejected_at_persist_boundary() -> None:
    overrides = {
        "auth": {
            "token_secret": "a" * 64,
            "email_password_resets": True,
        }
    }
    with pytest.raises(SettingsValidationError):
        sanitize_persisted_overrides(overrides)


def test_token_secret_incoming_is_rejected() -> None:
    with pytest.raises(SettingsValidationError):
        reject_restart_only_incoming({"auth": {"token_secret": "a" * 64}})


def test_preserve_live_token_secret_when_building_snapshot() -> None:
    live = MiraMediaConfig()
    live_secret = live.auth.token_secret
    snapshot = build_merged_validated_config(
        {"auth": {"email_password_resets": True}},
    )
    assert snapshot.auth.token_secret == live_secret


def test_export_strips_token_secret() -> None:
    exported = sanitize_export_overrides(
        {"auth": {"token_secret": "secret", "email_password_resets": True}}
    )
    assert "token_secret" not in exported.get("auth", {})


def test_validate_incoming_settings_update_rejects_unknown_section_key() -> None:
    with pytest.raises(ValidationError):
        validate_incoming_settings_update({"bogus": {"x": 1}})


def test_validate_incoming_rejects_unknown_nested_auth_field() -> None:
    with pytest.raises(ValidationError):
        validate_incoming_settings_update(
            {"auth": {"openid_connect": {"bogus_field": "x"}}}
        )


def test_validate_incoming_rejects_unknown_misc_nested_field() -> None:
    with pytest.raises(ValidationError):
        validate_incoming_settings_update(
            {"misc": {"naming": {"unknown_key": "value"}}}
        )


def test_join_frontend_path_normalizes_slashes() -> None:
    from miramedia.auth.runtime import join_frontend_path

    assert join_frontend_path("http://localhost:8080/", "web/dashboard") == (
        "http://localhost:8080/web/dashboard"
    )
    assert join_frontend_path("https://app.example.com", "/web/dashboard") == (
        "https://app.example.com/web/dashboard"
    )


def test_clear_path_rejects_restart_only_token_secret() -> None:
    from miramedia.settings.validation import (
        SettingsValidationError,
        reject_restart_only_clear_path,
    )

    with pytest.raises(SettingsValidationError, match="cannot be changed at runtime"):
        reject_restart_only_clear_path(["auth", "token_secret"])


def test_restart_only_paths_include_token_secret() -> None:
    assert ("auth", "token_secret") in RESTART_ONLY_OVERRIDE_PATHS


@pytest.mark.parametrize(
    ("session_lifetime", "accepted"),
    [
        (1, True),
        (60 * 60 * 24 * 366, True),
        (0, False),
        (-1, False),
        (60 * 60 * 24 * 366 + 1, False),
    ],
)
def test_session_lifetime_boundaries_in_settings_validation(
    session_lifetime: int,
    accepted: bool,
) -> None:
    overrides = {"auth": {"session_lifetime": session_lifetime}}
    if accepted:
        config = build_merged_validated_config(overrides)
        assert config.auth.session_lifetime == session_lifetime
        sanitize_persisted_overrides(overrides)
        payload = validate_incoming_settings_update(overrides)
        assert payload["auth"]["session_lifetime"] == session_lifetime
    else:
        with pytest.raises(SettingsValidationError):
            build_merged_validated_config(overrides)
        with pytest.raises(ValidationError):
            validate_incoming_settings_update(overrides)


def test_strip_restart_only_is_deep() -> None:
    overrides = copy.deepcopy(
        {"auth": {"token_secret": "x" * 64, "openid_connect": {"enabled": True}}}
    )
    stripped = strip_restart_only_overrides(overrides)
    assert "token_secret" not in stripped["auth"]
    assert stripped["auth"]["openid_connect"]["enabled"] is True

"""Validate settings overrides through the real Pydantic model graph."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ValidationError

from miramedia.config import MiraMediaConfig
from miramedia.settings.schemas import SystemSettingsUpdate
from miramedia.settings.service import SETTINGS_SECTIONS, deep_merge

RESTART_ONLY_OVERRIDE_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {("auth", "token_secret")}
)

SECRET_MASK = "********"  # noqa: S105 — mask sentinel, not a credential

# New config fields named password/api_key/api_token/client_secret/smtp_password/
# shim_api_key are auto-collected as secret override paths below.
CREDENTIAL_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "api_key",
        "api_token",
        "client_secret",
        "smtp_password",
        "shim_api_key",
    }
)

EXCLUDED_SECRET_OVERRIDE_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {("auth", "token_secret")}
)


class SettingsValidationError(Exception):
    """Prospective settings could not be validated."""


def _format_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = first.get("msg", "invalid value")
    return f"Invalid settings{': ' + loc if loc else ''}: {msg}"


def _nested_model_type(field_annotation: Any) -> type[BaseModel] | None:  # noqa: ANN401
    from typing import Union, get_args, get_origin

    origin = get_origin(field_annotation)
    if origin in (Union, types.UnionType):
        for arg in get_args(field_annotation):
            nested = _nested_model_type(arg)
            if nested is not None:
                return nested
        return None
    if isinstance(field_annotation, type) and issubclass(field_annotation, BaseModel):
        return field_annotation
    return None


import types  # noqa: E402 — used by _nested_model_type


def _derive_secret_override_paths() -> frozenset[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    isolated = MiraMediaConfig.load_isolated()

    def walk(model: type[BaseModel], prefix: tuple[str, ...]) -> None:
        for name, field in model.model_fields.items():
            path = (*prefix, name)
            nested = _nested_model_type(field.annotation)
            if nested is not None:
                walk(nested, path)
            elif (
                name in CREDENTIAL_FIELD_NAMES
                and path not in EXCLUDED_SECRET_OVERRIDE_PATHS
            ):
                paths.add(path)

    for section in SETTINGS_SECTIONS:
        section_model = type(getattr(isolated, section))
        walk(section_model, (section,))

    return frozenset(paths)


SECRET_OVERRIDE_PATHS: frozenset[tuple[str, ...]] = _derive_secret_override_paths()

CONNECTION_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {"host", "port", "url", "base_path", "https"}
)


def _bool_expected(annotation: Any) -> bool:  # noqa: ANN401
    from typing import Union, get_args, get_origin

    if annotation is bool:
        return True
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return any(_bool_expected(arg) for arg in get_args(annotation))
    return False


def _validate_override_value_types(
    overrides: dict,
    model: type[BaseModel],
    path: tuple[str, ...],
) -> None:
    for key, value in overrides.items():
        if key not in model.model_fields:
            continue
        field = model.model_fields[key]
        field_path = (*path, key)
        if isinstance(value, dict):
            nested = _nested_model_type(field.annotation)
            if nested is not None:
                _validate_override_value_types(value, nested, field_path)
            continue
        if _bool_expected(field.annotation) and isinstance(value, str):
            msg = f"Invalid boolean setting: {'.'.join(field_path)}"
            raise SettingsValidationError(msg)


def _reject_unknown_override_keys(
    overrides: dict,
    model: type[BaseModel],
    path: tuple[str, ...],
) -> None:
    if not isinstance(overrides, dict):
        msg = f"Invalid settings section {'.'.join(path)}: expected object"
        raise SettingsValidationError(msg)
    for key, value in overrides.items():
        if key not in model.model_fields:
            msg = f"Unknown setting: {'.'.join((*path, key))}"
            raise SettingsValidationError(msg)
        if isinstance(value, dict):
            nested = _nested_model_type(model.model_fields[key].annotation)
            if nested is not None:
                _reject_unknown_override_keys(value, nested, (*path, key))


def mask_secret_values(tree: dict) -> dict:
    """Deep-copy *tree* and replace stored credential leaves with ``SECRET_MASK``."""
    result = copy.deepcopy(tree)
    for path in SECRET_OVERRIDE_PATHS:
        node: Any = result
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                break
            node = node[key]
        else:
            if isinstance(node, dict) and path[-1] in node:
                value = node[path[-1]]
                if isinstance(value, str) and value:
                    node[path[-1]] = SECRET_MASK
    return result


def masked_credential_with_changed_target(
    config: dict,
    effective_section: dict,
    section_path: tuple[str, ...],
) -> bool:
    """True when a masked credential is present but connection identity fields differ."""
    credential_keys = {
        path[-1]
        for path in SECRET_OVERRIDE_PATHS
        if len(path) > len(section_path) and path[: len(section_path)] == section_path
    }
    has_masked_credential = any(
        config.get(key) == SECRET_MASK for key in credential_keys if key in config
    )
    if not has_masked_credential:
        return False
    for field in CONNECTION_IDENTITY_FIELDS:
        if field in config and config[field] != effective_section.get(field):
            return True
    return False


def resolve_masked_config(
    config: dict,
    effective_section: dict,
    section_path: tuple[str, ...] | None = None,
) -> dict:
    """Replace mask sentinels in *config* with stored values from *effective_section*.

    Only credential leaves listed in ``SECRET_OVERRIDE_PATHS`` under *section_path*
    are substituted; other sentinels are left unchanged.
    """
    result = copy.deepcopy(config)
    prefix = section_path or ()

    def _resolve(
        node: dict[str, Any],
        effective: dict[str, Any],
        path_prefix: tuple[str, ...],
    ) -> None:
        for key, value in node.items():
            full_path = (*path_prefix, key)
            if value == SECRET_MASK:
                if full_path in SECRET_OVERRIDE_PATHS:
                    stored = effective.get(key, "")
                    node[key] = stored if isinstance(stored, str) else ""
            elif isinstance(value, dict):
                nested = effective.get(key)
                if isinstance(nested, dict):
                    _resolve(value, nested, full_path)

    if isinstance(result, dict) and isinstance(effective_section, dict):
        _resolve(result, effective_section, prefix)
    return result


def strip_masked_values(patch: dict) -> dict:
    """Remove credential leaves set to the mask sentinel (unchanged on write)."""
    result = copy.deepcopy(patch)
    for path in SECRET_OVERRIDE_PATHS:
        node: Any = result
        stack: list[tuple[dict[str, Any], str]] = []
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                break
            stack.append((node, key))
            node = node[key]
        else:
            if isinstance(node, dict) and path[-1] in node:
                if node[path[-1]] == SECRET_MASK:
                    del node[path[-1]]
                    for parent, key in reversed(stack):
                        child = parent[key]
                        if isinstance(child, dict) and not child:
                            del parent[key]
                        else:
                            break
    return result


def strip_restart_only_overrides(overrides: dict) -> dict:
    result = copy.deepcopy(overrides)
    for path in RESTART_ONLY_OVERRIDE_PATHS:
        _delete_path(result, path)
    return result


def _delete_path(root: dict, path: tuple[str, ...]) -> None:
    node: Any = root
    stack: list[tuple[dict, str]] = []
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return
        stack.append((node, key))
        node = node[key]
    if isinstance(node, dict) and path[-1] in node:
        del node[path[-1]]
    for parent, key in reversed(stack):
        if isinstance(parent[key], dict) and not parent[key]:
            del parent[key]


def _get_path(root: dict, path: tuple[str, ...]) -> Any:  # noqa: ANN401
    node: Any = root
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _set_path(root: dict, path: tuple[str, ...], value: Any) -> None:  # noqa: ANN401
    node: Any = root
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = copy.deepcopy(value)


def carry_forward_secrets(existing: dict, merged: dict) -> dict:
    """Copy stored secret overrides into *merged* when absent from an import."""
    result = copy.deepcopy(merged)
    for path in SECRET_OVERRIDE_PATHS:
        existing_value = _get_path(existing, path)
        if not existing_value:
            continue
        if _get_path(result, path) is None:
            _set_path(result, path, existing_value)
    return result


def reject_restart_only_incoming(overrides: dict) -> None:
    for path in RESTART_ONLY_OVERRIDE_PATHS:
        node: Any = overrides
        for key in path:
            if not isinstance(node, dict) or key not in node:
                break
            node = node[key]
        else:
            msg = f"Setting {'.'.join(path)} cannot be changed at runtime"
            raise SettingsValidationError(msg)


def reject_restart_only_clear_path(path: list[str]) -> None:
    if tuple(path) in RESTART_ONLY_OVERRIDE_PATHS:
        dotted = ".".join(path)
        msg = f"Setting {dotted} cannot be changed at runtime"
        raise SettingsValidationError(msg)


def validate_incoming_settings_update(data: dict) -> dict:
    model = SystemSettingsUpdate.model_validate(data)
    return model.model_dump(mode="json", exclude_unset=True)


def preserve_live_token_secret(config: MiraMediaConfig) -> MiraMediaConfig:
    live_secret = MiraMediaConfig().auth.token_secret
    config.auth = config.auth.model_copy(update={"token_secret": live_secret})
    return config


def build_merged_validated_config(overrides: dict | None = None) -> MiraMediaConfig:
    """Deep-merge overrides into an isolated TOML baseline and validate strictly."""
    baseline = MiraMediaConfig.load_isolated()
    if not overrides:
        return preserve_live_token_secret(baseline)

    sanitized = strip_restart_only_overrides(overrides)
    isolated = MiraMediaConfig.load_isolated()
    for section in SETTINGS_SECTIONS:
        section_model_type = type(getattr(isolated, section))
        base_dict = getattr(baseline, section).model_dump(mode="json")
        if section in sanitized:
            _reject_unknown_override_keys(
                sanitized[section], section_model_type, (section,)
            )
            _validate_override_value_types(
                sanitized[section], section_model_type, (section,)
            )
            merged = deep_merge(base_dict, sanitized[section])
        else:
            merged = base_dict
        try:
            validated = section_model_type.model_validate(merged, strict=True)
        except ValidationError as exc:
            raise SettingsValidationError(_format_validation_error(exc)) from exc
        setattr(isolated, section, validated)
    return preserve_live_token_secret(isolated)


def sanitize_persisted_overrides(overrides: dict) -> dict:
    reject_restart_only_incoming(overrides)
    sanitized = strip_restart_only_overrides(overrides)
    build_merged_validated_config(sanitized)
    return sanitized


def sanitize_export_overrides(overrides: dict) -> dict:
    result = strip_restart_only_overrides(overrides)
    for path in SECRET_OVERRIDE_PATHS:
        _delete_path(result, path)
    return result

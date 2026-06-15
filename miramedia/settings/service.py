from __future__ import annotations

import copy
import logging
import threading
import types
from enum import Enum
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from miramedia.config import MiraMediaConfig

log = logging.getLogger(__name__)

# Serializes the transient swap-out of the MiraMediaConfig singleton when reading TOML
# defaults so two settings requests can't race and leak a duplicate instance.
_singleton_swap_lock = threading.Lock()


def deep_merge(base: dict, overrides: dict) -> dict:
    """Deep merge overrides into base dict. Overrides win for leaf values."""
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def strip_none(d: Any) -> Any:  # noqa: ANN401 — recurses over arbitrary config leaves
    """Recursively remove None values from a dict (for partial updates)."""
    if not isinstance(d, dict):
        return d
    return {k: strip_none(v) for k, v in d.items() if v is not None}


def diff_against_defaults(incoming: dict, defaults: dict) -> dict:
    """Return only the keys/values in incoming that differ from defaults.

    This prevents the full effective config from being stored as overrides
    when the frontend sends everything back.
    """
    result: dict = {}
    for key, value in incoming.items():
        default_value = defaults.get(key)
        if isinstance(value, dict) and isinstance(default_value, dict):
            nested = diff_against_defaults(value, default_value)
            if nested:
                result[key] = nested
        elif value != default_value:
            result[key] = value
    return result


REDACTED_FIELDS = {
    "auth": {"token_secret"},
}

# Fields hidden from the search schema and the settings UI because they're deprecated or
# managed elsewhere. Their dotted paths must match the singleton attribute path.
DEPRECATED_FIELDS: set[tuple[str, ...]] = {
    ("auth", "admin_emails"),
}


def get_effective_config(overrides: dict) -> dict:
    """Load config from TOML and merge DB overrides on top."""
    config = MiraMediaConfig()
    # Sections we expose (excluding database)
    sections = [
        "misc",
        "auth",
        "notifications",
        "torrents",
        "indexers",
        "metadata",
        "requests",
        "subtitles",
        "updates",
        "cloudflare",
        "imports",
    ]
    result = {}
    for section in sections:
        section_config = getattr(config, section)
        section_dict = _config_to_dict(section_config)
        if section in overrides:
            section_dict = deep_merge(section_dict, overrides[section])
        # Remove sensitive fields
        for field in REDACTED_FIELDS.get(section, set()):
            section_dict.pop(field, None)
        result[section] = section_dict
    return result


def _humanize(name: str) -> str:
    """Turn ``snake_case_field`` into ``Snake Case Field`` for UI labels."""
    return name.replace("_", " ").strip().title()


def _python_type_label(annotation: Any) -> str:  # noqa: ANN401
    """Render a short, human-readable type label for the schema."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_label(non_none[0])
        return " | ".join(_python_type_label(a) for a in non_none)
    if origin is list:
        args = get_args(annotation)
        return f"list[{_python_type_label(args[0])}]" if args else "list"
    if origin is dict:
        return "dict"
    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return f"enum[{', '.join(m.name for m in annotation)}]"
        if issubclass(annotation, Path):
            return "path"
        return annotation.__name__
    return str(annotation)


def _walk_schema(model: Any, path: list[str], out: list[dict]) -> None:  # noqa: ANN401
    """Recursively flatten a pydantic model's fields into search-friendly entries."""
    if not hasattr(model, "model_fields"):
        return
    for field_name, field_info in model.model_fields.items():
        if field_name.startswith("_"):
            continue
        current_path = [*path, field_name]
        annotation = field_info.annotation
        # Unwrap Optional/Union for inspection but keep label honest.
        nested_model: Any = None
        origin = get_origin(annotation)
        candidate_args: list[Any] = []
        if origin in (Union, types.UnionType):
            candidate_args = list(get_args(annotation))
        else:
            candidate_args = [annotation]
        for arg in candidate_args:
            if (
                isinstance(arg, type)
                and not issubclass(arg, Enum)
                and not issubclass(arg, Path)
                and hasattr(arg, "model_fields")
            ):
                nested_model = arg
                break
        if nested_model is not None:
            _walk_schema(nested_model, current_path, out)
            continue
        try:
            default_value = (
                getattr(model, field_name, None) if not callable(model) else None
            )
        except Exception:
            default_value = None
        if default_value is not None and not isinstance(
            default_value, (str, int, float, bool, list, dict)
        ):
            try:
                default_value = _serialize_values(
                    default_value
                    if isinstance(default_value, (list, dict))
                    else str(default_value)
                )
            except Exception:
                default_value = None
        # Skip secret fields from schema (they're listed by name in REDACTED_FIELDS).
        if (
            len(current_path) >= 2
            and current_path[0] in REDACTED_FIELDS
            and current_path[-1] in REDACTED_FIELDS[current_path[0]]
        ):
            continue
        if tuple(current_path) in DEPRECATED_FIELDS:
            continue
        out.append(
            {
                "path": current_path,
                "section": current_path[0],
                "key": ".".join(current_path),
                "label": _humanize(field_name),
                "description": field_info.description or "",
                "type": _python_type_label(annotation),
            }
        )


def get_settings_schema() -> list[dict]:
    """Return a flat searchable index of every settings leaf field for the UI search box."""
    config = MiraMediaConfig()
    sections = [
        "misc",
        "auth",
        "notifications",
        "torrents",
        "indexers",
        "metadata",
        "requests",
        "subtitles",
        "updates",
        "cloudflare",
        "imports",
    ]
    out: list[dict] = []
    for section in sections:
        _walk_schema(getattr(config, section), [section], out)
    return out


def get_toml_defaults() -> dict:
    """Return TOML-only defaults (no DB overrides applied) for the UI to show 'Default: ...' tooltips.

    Builds a transient fresh instance to bypass the singleton's already-applied overrides.
    """
    with _singleton_swap_lock:
        saved_instance = MiraMediaConfig._instance
        saved_initialized = MiraMediaConfig._initialized
        MiraMediaConfig._instance = None
        MiraMediaConfig._initialized = False
        try:
            fresh = MiraMediaConfig()
        finally:
            MiraMediaConfig._instance = saved_instance
            MiraMediaConfig._initialized = saved_initialized

    sections = [
        "misc",
        "auth",
        "notifications",
        "torrents",
        "indexers",
        "metadata",
        "requests",
        "subtitles",
        "updates",
        "cloudflare",
        "imports",
    ]
    result: dict = {}
    for section in sections:
        section_dict = _config_to_dict(getattr(fresh, section))
        for field in REDACTED_FIELDS.get(section, set()):
            section_dict.pop(field, None)
        result[section] = section_dict
    return result


def _config_to_dict(obj: Any, *, json_mode: bool = False) -> Any:  # noqa: ANN401 — arbitrary pydantic model in, JSON-serializable out
    """Convert a pydantic model to a JSON-serializable dict."""
    if hasattr(obj, "model_dump"):
        if json_mode:
            return obj.model_dump(mode="json")
        d = obj.model_dump()
        return _serialize_values(d)
    return obj


def _serialize_values(d: Any) -> Any:  # noqa: ANN401 — recurses over arbitrary dumped values
    """Ensure all values are JSON-serializable."""
    if isinstance(d, dict):
        return {k: _serialize_values(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_serialize_values(v) for v in d]
    if isinstance(d, Enum):
        # Enum — use the name (e.g., "fullhd") not the value (e.g., 2)
        return d.name
    if d is None:
        return None
    if not isinstance(d, (str, int, float, bool)):
        return str(d)
    return d


def revert_field_to_toml_default(path: list[str]) -> None:
    """Reset a single dotted-path field on the in-memory singleton back to its TOML default.

    Builds a transient fresh instance to read the TOML default, then setattrs on the real
    singleton so external references (e.g. ``cfg.torrents``) remain valid.
    """
    if not path:
        return
    with _singleton_swap_lock:
        saved_instance = MiraMediaConfig._instance
        saved_initialized = MiraMediaConfig._initialized
        MiraMediaConfig._instance = None
        MiraMediaConfig._initialized = False
        try:
            fresh = MiraMediaConfig()
        finally:
            MiraMediaConfig._instance = saved_instance
            MiraMediaConfig._initialized = saved_initialized

    if saved_instance is None:
        return

    fresh_node: Any = fresh
    real_node: Any = saved_instance
    for key in path[:-1]:
        if not hasattr(fresh_node, key) or not hasattr(real_node, key):
            return
        fresh_node = getattr(fresh_node, key)
        real_node = getattr(real_node, key)
    leaf = path[-1]
    if not hasattr(fresh_node, leaf) or not hasattr(real_node, leaf):
        return
    try:
        setattr(real_node, leaf, getattr(fresh_node, leaf))
    except Exception:
        log.exception(
            "Failed to revert config field %s to TOML default", ".".join(path)
        )


def apply_overrides_to_config(config: MiraMediaConfig, overrides: dict) -> None:
    """Apply DB overrides to the in-memory config singleton.

    Uses setattr to apply individual field overrides, preserving type coercion.
    """
    sections = [
        "misc",
        "auth",
        "notifications",
        "torrents",
        "indexers",
        "metadata",
        "requests",
        "subtitles",
        "updates",
        "cloudflare",
        "imports",
    ]
    for section in sections:
        if section not in overrides:
            continue
        section_config = getattr(config, section)
        try:
            _apply_nested_overrides(section_config, overrides[section])
        except Exception:
            log.exception(f"Failed to apply overrides for section '{section}'")


def _apply_nested_overrides(obj: Any, overrides: dict) -> None:  # noqa: ANN401 — arbitrary nested pydantic model
    """Recursively apply overrides to a pydantic model via setattr."""
    for key, value in overrides.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if isinstance(value, dict) and hasattr(current, "__dict__"):
            _apply_nested_overrides(current, value)
        else:
            # For enum fields, try to convert string name to enum member
            field_info = (
                obj.model_fields.get(key) if hasattr(obj, "model_fields") else None
            )
            if field_info and isinstance(value, str):
                annotation = field_info.annotation
                # Handle Optional[Enum] and Enum types
                origin = getattr(annotation, "__origin__", None)
                if origin is not None:
                    args = getattr(annotation, "__args__", ())
                    for arg in args:
                        if isinstance(arg, type) and issubclass(arg, Enum):
                            annotation = arg
                            break
                if isinstance(annotation, type) and issubclass(annotation, Enum):
                    try:
                        value = annotation[value]
                    except KeyError:
                        pass
            # Coerce strings to Path for Path-typed fields
            if field_info and isinstance(value, str):
                annotation = field_info.annotation
                origin = getattr(annotation, "__origin__", None)
                if origin is not None:
                    args = getattr(annotation, "__args__", ())
                    for arg in args:
                        if isinstance(arg, type) and issubclass(arg, Path):
                            annotation = arg
                            break
                if isinstance(annotation, type) and issubclass(annotation, Path):
                    value = Path(value)
            # Coerce list[dict] to list[Model] for list-of-pydantic-model fields
            if field_info and isinstance(value, list):
                annotation = field_info.annotation
                # Unwrap Optional/Union (e.g. list[Model] | None)
                if get_origin(annotation) in (Union, types.UnionType):
                    for arg in get_args(annotation):
                        if get_origin(arg) is list:
                            annotation = arg
                            break
                if get_origin(annotation) is list:
                    type_args = get_args(annotation)
                    if type_args:
                        item_type = type_args[0]
                        if isinstance(item_type, type) and hasattr(
                            item_type, "model_validate"
                        ):
                            value = [
                                item_type.model_validate(item)
                                if isinstance(item, dict)
                                else item
                                for item in value
                            ]
            setattr(obj, key, value)

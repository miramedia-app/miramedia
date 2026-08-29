"""Dependency-neutral settings section list and dict composition primitives."""

from __future__ import annotations

import copy
from typing import Any

SETTINGS_SECTIONS = (
    "misc",
    "auth",
    "notifications",
    "torrents",
    "indexers",
    "metadata",
    "requests",
    "watchlists",
    "subtitles",
    "updates",
    "cloudflare",
    "imports",
    "streams",
    "playback",
)


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

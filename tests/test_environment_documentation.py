"""Keep .env.example aligned with documented runtime defaults."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
STARTUP_SOURCE = REPO_ROOT / "miramedia" / "startup.py"
CACHE_SOURCE = REPO_ROOT / "miramedia" / "metadata" / "cache.py"
NOTIFY_SOURCE = REPO_ROOT / "miramedia" / "notifications" / "manager.py"


def _env_example_value(name: str) -> int:
    prefix = f"{name}="
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(line.split("=", 1)[1].strip())
    msg = f"{name} missing from .env.example"
    raise AssertionError(msg)


def _getenv_int_default(source: Path, env_name: str) -> int:
    patterns = (
        rf'getenv\("{re.escape(env_name)}", "(\d+)"\)',
        rf'environ\.get\("{re.escape(env_name)}", "(\d+)"\)',
    )
    text = source.read_text(encoding="utf-8")
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is not None:
            return int(match.group(1))
    msg = f"could not find getenv default for {env_name} in {source}"
    raise AssertionError(msg)


def _taskiq_lane_defaults() -> tuple[int, int]:
    text = STARTUP_SOURCE.read_text(encoding="utf-8")
    interactive_fb = int(
        re.search(r'interactive_env or "(\d+)"', text).group(1)  # type: ignore[union-attr]
    )
    background_fb = int(
        re.search(r'background_env or "(\d+)"', text).group(1)  # type: ignore[union-attr]
    )
    else_match = re.search(
        r"else:\n\s+interactive_max = (\d+)\n\s+background_max = (\d+)",
        text,
    )
    if else_match is None:
        msg = "could not find TaskIQ lane fallback defaults in startup.py"
        raise AssertionError(msg)
    interactive_else = int(else_match.group(1))
    background_else = int(else_match.group(2))
    assert interactive_fb == interactive_else
    assert background_fb == background_else
    return interactive_else, background_else


@pytest.mark.parametrize(
    ("env_name", "expected"),
    [
        ("MIRAMEDIA_INTERACTIVE_TASK_LIMIT", None),
        ("MIRAMEDIA_BACKGROUND_TASK_LIMIT", None),
        (
            "MIRAMEDIA_METADATA_CACHE_MAXSIZE",
            _getenv_int_default(CACHE_SOURCE, "MIRAMEDIA_METADATA_CACHE_MAXSIZE"),
        ),
        (
            "MIRAMEDIA_NOTIFY_SUPPRESS_SECONDS",
            _getenv_int_default(NOTIFY_SOURCE, "MIRAMEDIA_NOTIFY_SUPPRESS_SECONDS"),
        ),
    ],
)
def test_env_example_matches_runtime_defaults(
    env_name: str,
    expected: int | None,
) -> None:
    if env_name == "MIRAMEDIA_INTERACTIVE_TASK_LIMIT":
        expected, _ = _taskiq_lane_defaults()
    elif env_name == "MIRAMEDIA_BACKGROUND_TASK_LIMIT":
        _, expected = _taskiq_lane_defaults()
    assert expected is not None
    assert _env_example_value(env_name) == expected

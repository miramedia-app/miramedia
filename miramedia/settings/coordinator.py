"""Process-local settings commit/apply coordination."""

from __future__ import annotations

import asyncio

_settings_coordinator_lock = asyncio.Lock()


def get_settings_coordinator_lock() -> asyncio.Lock:
    """Lock for mutation CAS+apply and reload final apply (never across OIDC I/O)."""
    return _settings_coordinator_lock

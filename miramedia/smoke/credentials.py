from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SmokeAdminCredentials:
    """Disposable admin credentials for the real-browser smoke path."""

    email: str
    password: str

"""Thread-safe last-known-good mirror ordering for native indexer sites."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from urllib.parse import urlparse


def is_allowed_mirror_origin(origin: str, mirrors: Iterable[str]) -> bool:
    """True when ``origin`` is an https URL whose origin is in ``mirrors``.

    Used to reject redirect-derived origins that leave the configured mirror
    set — a malicious mirror must not be able to steer detail fetches to an
    arbitrary host.
    """
    parsed = urlparse(origin)
    if parsed.scheme != "https":
        return False
    normalized = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return normalized in {m.rstrip("/") for m in mirrors}


class MirrorPreference:
    """Instance-local hint for which mirror to try first.

    Callers must iterate the entire snapshot from :meth:`ordered`; the preferred
    mirror is an optimization only and must never be used to skip mirrors.
    """

    def __init__(self, mirrors: Iterable[str]) -> None:
        seen: set[str] = set()
        normalized: list[str] = []
        for mirror in mirrors:
            if mirror in seen:
                continue
            seen.add(mirror)
            normalized.append(mirror)
        if not normalized:
            msg = "mirror list must not be empty"
            raise ValueError(msg)
        self._mirrors = tuple(normalized)
        self._preferred = self._mirrors[0]
        self._lock = threading.Lock()

    def ordered(self) -> tuple[str, ...]:
        with self._lock:
            preferred = self._preferred
            mirrors = self._mirrors
        if preferred == mirrors[0]:
            return mirrors
        rest = tuple(mirror for mirror in mirrors if mirror != preferred)
        return (preferred, *rest)

    def mark_success(self, mirror: str) -> None:
        """Move ``mirror`` to the front of future ``ordered()`` snapshots.

        Unknown mirrors are ignored: preference is a pure optimization and a
        stale/redirect-derived origin must never fail an otherwise successful
        request.
        """
        if mirror not in self._mirrors:
            return
        with self._lock:
            self._preferred = mirror

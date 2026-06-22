from __future__ import annotations

import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, cast

import httpx
from cachetools import TTLCache

from miramedia.config import MiraMediaConfig
from miramedia.updates.schemas import (
    ApplyState,
    UpdateInfo,
    UpdateStatusState,
    VersionInfo,
)

log = logging.getLogger(__name__)

_GITHUB_BASE = "https://api.github.com"
_USER_AGENT = "MiraMedia-UpdateChecker/1.0"


def _strip_v(s: str) -> str:
    return s[1:] if s and s[0].lower() == "v" else s


_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _parse_semver(s: str) -> tuple[int, int, int, str] | None:
    m = _SEMVER_RE.match(_strip_v(s or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or ""


def is_semver(s: str | None) -> bool:
    if not s:
        return False
    return _parse_semver(s) is not None


def compare_semver(a: str, b: str) -> int:
    """Return 1 if a>b, -1 if a<b, 0 if equal. Falls back to localeCompare-numeric."""
    pa = _parse_semver(a)
    pb = _parse_semver(b)
    if pa and pb:
        for i in range(3):
            if pa[i] != pb[i]:
                return 1 if pa[i] > pb[i] else -1
        # prerelease: missing prerelease > present prerelease (stable beats rc)
        if pa[3] == pb[3]:
            return 0
        if not pa[3]:
            return 1
        if not pb[3]:
            return -1
        return 1 if pa[3] > pb[3] else -1
    # Fallback for non-semver tags: numeric-aware lexical
    if a == b:
        return 0
    return 1 if _natural_gt(a, b) else -1


def _natural_gt(a: str, b: str) -> bool:
    """Numeric-aware lexical compare. Returns True when a > b.

    Uses (kind, value) tuples so heterogeneous string/int positions never get
    compared directly (which raises TypeError under Python 3).
    """

    def key(s: str) -> list[tuple[int, int, str]]:
        parts: list[tuple[int, int, str]] = []
        for t in re.split(r"(\d+)", s):
            if not t:
                continue
            if t.isdigit():
                parts.append((0, int(t), ""))
            else:
                parts.append((1, 0, t))
        return parts

    try:
        return key(a) > key(b)
    except TypeError:
        return a > b


_NEGATIVE_CACHE_SENTINEL = "__miss__"
_NEGATIVE_CACHE_TTL_SECONDS = 600  # 10 min; short enough to retry if repo appears


class _UpdateCheckCache:
    """TTL cache for the latest release lookup. Thread-safe.

    Caches both hits (release dict) and misses (sentinel value) so a flaky
    or 404'd GitHub endpoint doesn't get re-hammered by every UI poll. Hits
    use the configured ``cache_ttl_seconds``; misses use a shorter window so
    the system recovers quickly once the upstream is reachable again.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self._cache: TTLCache = TTLCache(maxsize=4, ttl=ttl_seconds)
        self._miss_cache: TTLCache = TTLCache(
            maxsize=4, ttl=_NEGATIVE_CACHE_TTL_SECONDS
        )
        self._lock = threading.Lock()
        self._last_checked_at: datetime | None = None

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._cache.get(key)

    def is_missing(self, key: str) -> bool:
        with self._lock:
            return self._miss_cache.get(key) == _NEGATIVE_CACHE_SENTINEL

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._cache[key] = value
            self._miss_cache.pop(key, None)
            self._last_checked_at = datetime.now(UTC)

    def set_missing(self, key: str) -> None:
        with self._lock:
            self._miss_cache[key] = _NEGATIVE_CACHE_SENTINEL
            self._last_checked_at = datetime.now(UTC)

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self._miss_cache.clear()

    @property
    def last_checked_at(self) -> datetime | None:
        return self._last_checked_at

    def reconfigure(self, ttl_seconds: int) -> None:
        with self._lock:
            self._cache = TTLCache(maxsize=4, ttl=ttl_seconds)
            self._miss_cache = TTLCache(maxsize=4, ttl=_NEGATIVE_CACHE_TTL_SECONDS)


class UpdateService:
    """Singleton update service: fetches GitHub releases, caches, exposes apply state."""

    _instance: UpdateService | None = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> Self:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cast("Self", cls._instance)

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        cfg = MiraMediaConfig().updates
        self._cache = _UpdateCheckCache(ttl_seconds=cfg.cache_ttl_seconds)
        self._apply_state = ApplyState(state=UpdateStatusState.idle)
        self._apply_lock = threading.Lock()
        self._initialized = True

    # ----- version helpers -----

    @staticmethod
    def get_current_version() -> str | None:
        v = os.getenv("PUBLIC_VERSION")
        if v in (None, "", "dev"):
            return v or None
        return v

    @staticmethod
    def get_version_info() -> VersionInfo:
        cfg = MiraMediaConfig().updates
        return VersionInfo(
            version=UpdateService.get_current_version(),
            image=f"{cfg.image_repository}:{cfg.image_tag}",
            base_path=os.getenv("BASE_PATH") or None,
        )

    # ----- release fetching -----

    def _fetch_latest_release(self) -> dict | None:
        cfg = MiraMediaConfig().updates
        if not cfg.enabled:
            return None
        url = f"{_GITHUB_BASE}/repos/{cfg.repo}/releases"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        }
        try:
            # trust_env=False: ignore HTTPS_PROXY/HTTP_PROXY env vars. Update
            # checks should always go direct to GitHub regardless of the host's
            # proxy config (which may be malformed, e.g. invalid IPv6).
            with httpx.Client(trust_env=False) as client:
                resp = client.get(
                    url,
                    headers=headers,
                    timeout=cfg.request_timeout_seconds,
                    params={"per_page": 20},
                )
            resp.raise_for_status()
            releases = resp.json()
        except httpx.HTTPStatusError as exc:
            # 404 = misconfigured/unset repo. Don't spam WARN — DEBUG only.
            level = (
                logging.DEBUG if exc.response.status_code == 404 else logging.WARNING
            )
            log.log(level, "update check failed: HTTPStatusError: %s", exc)
            return None
        except Exception as exc:
            # Catch broadly: httpx.HTTPError doesn't cover DNS / SSL / proxy errors
            log.warning("update check failed: %s: %s", type(exc).__name__, exc)
            return None

        for rel in releases:
            if rel.get("draft"):
                continue
            if rel.get("prerelease") and not cfg.include_prereleases:
                continue
            return rel
        return None

    def get_update_info(self, force: bool = False) -> UpdateInfo:
        cfg = MiraMediaConfig().updates
        current = self.get_current_version()

        if not cfg.enabled:
            return UpdateInfo(
                enabled=False,
                current_version=current,
                latest_version=None,
                update_available=False,
                release_url=None,
                release_notes=None,
                published_at=None,
                last_checked_at=self._cache.last_checked_at,
                repo=cfg.repo,
                apply_supported=self.is_apply_supported(),
            )

        cache_key = f"{cfg.repo}:{cfg.include_prereleases}"
        cached = None if force else self._cache.get(cache_key)
        if cached is None and not force and self._cache.is_missing(cache_key):
            # Recently failed; don't re-hit GitHub until the negative TTL
            # expires. Surface the empty-update response below.
            pass
        elif cached is None:
            release = self._fetch_latest_release()
            if release is None:
                # Remember the failure so the next caller within the
                # negative-cache window doesn't fire another request.
                self._cache.set_missing(cache_key)
            else:
                cached = {
                    "tag_name": release.get("tag_name"),
                    "html_url": release.get("html_url"),
                    "body": release.get("body"),
                    "published_at": release.get("published_at"),
                }
                self._cache.set(cache_key, cached)

        if cached is None:
            return UpdateInfo(
                enabled=True,
                current_version=current,
                latest_version=None,
                update_available=False,
                release_url=None,
                release_notes=None,
                published_at=None,
                last_checked_at=self._cache.last_checked_at,
                repo=cfg.repo,
                apply_supported=self.is_apply_supported(),
            )

        latest_tag = cached.get("tag_name") or ""
        latest = _strip_v(latest_tag)
        update_available = False
        if current and is_semver(current) and is_semver(latest):
            update_available = compare_semver(latest, current) > 0
        elif current and latest and is_semver(latest):
            # Current is non-semver (e.g. "dev", a git SHA, unset). Don't guess.
            update_available = False
        elif current and latest:
            update_available = _natural_gt(latest, current)

        published_at = None
        pub = cached.get("published_at")
        if pub:
            try:
                published_at = datetime.fromisoformat(pub)
            except ValueError:
                published_at = None

        return UpdateInfo(
            enabled=True,
            current_version=current,
            # Display the v-stripped form so it matches current_version (which is
            # bare, e.g. "1.0.1"); the raw tag ("v1.0.0") would read as a mismatch.
            latest_version=latest or None,
            update_available=update_available,
            release_url=cached.get("html_url"),
            release_notes=cached.get("body"),
            published_at=published_at,
            last_checked_at=self._cache.last_checked_at,
            repo=cfg.repo,
            apply_supported=self.is_apply_supported(),
        )

    def invalidate_cache(self) -> None:
        self._cache.invalidate()

    # ----- apply support -----

    @staticmethod
    def is_apply_supported() -> bool:
        cfg = MiraMediaConfig().updates
        if not cfg.allow_in_app_apply:
            return False
        return Path(cfg.docker_socket_path).exists()

    def get_apply_state(self) -> ApplyState:
        with self._apply_lock:
            return self._apply_state.model_copy(deep=True)

    def _set_apply_state(
        self,
        state: UpdateStatusState | None = None,
        target_version: str | None = None,
        error: str | None = None,
        log_line: str | None = None,
        finished: bool = False,
    ) -> None:
        with self._apply_lock:
            if state is not None:
                self._apply_state.state = state
            if target_version is not None:
                self._apply_state.target_version = target_version
            if error is not None:
                self._apply_state.error = error
            if log_line is not None:
                self._apply_state.log = ([*self._apply_state.log, log_line])[-50:]
            if finished:
                self._apply_state.finished_at = datetime.now(UTC)

    def trigger_apply(self, target_tag: str | None = None) -> tuple[bool, str | None]:
        """Kick off a background apply. Returns (accepted, detail)."""
        if not self.is_apply_supported():
            return (
                False,
                "in-app apply is not supported (config disabled or docker socket missing)",
            )

        with self._apply_lock:
            if self._apply_state.state in (
                UpdateStatusState.checking,
                UpdateStatusState.pulling,
                UpdateStatusState.restarting,
            ):
                return (
                    False,
                    f"apply already in progress (state={self._apply_state.state.value})",
                )
            self._apply_state = ApplyState(
                state=UpdateStatusState.pulling,
                target_version=target_tag,
                started_at=datetime.now(UTC),
                log=[],
            )

        thread = threading.Thread(
            target=self._run_apply,
            args=(target_tag,),
            name="update-apply",
            daemon=True,
        )
        thread.start()
        return True, None

    def _run_apply(self, target_tag: str | None) -> None:
        from miramedia.updates.docker_apply import perform_docker_apply

        cfg = MiraMediaConfig().updates
        tag = target_tag or cfg.image_tag
        try:
            self._set_apply_state(log_line=f"pulling {cfg.image_repository}:{tag}")
            perform_docker_apply(
                socket_path=cfg.docker_socket_path,
                image_repository=cfg.image_repository,
                image_tag=tag,
                container_name=cfg.container_name,
                on_log=lambda line: self._set_apply_state(log_line=line),
                on_state=lambda s: self._set_apply_state(state=s),
            )
            self._set_apply_state(state=UpdateStatusState.applied, finished=True)
        except Exception as exc:
            log.exception("update apply failed")
            self._set_apply_state(
                state=UpdateStatusState.failed,
                error=str(exc),
                finished=True,
            )

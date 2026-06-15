"""Connection/auth test handlers for the settings page integrations.

Each handler receives a dict pulled from the live form state and returns an
``IntegrationTestResult``. Tests must be best-effort and fail closed — the goal is to give
operators a quick "yes/no" without persisting anything. Avoid long timeouts so a single
broken integration can't block the UI.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from typing import Any
from urllib.parse import urljoin

import requests
from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0


class IntegrationTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None


def _ok(message: str, started: float) -> IntegrationTestResult:
    return IntegrationTestResult(
        ok=True,
        message=message,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def _err(message: str, started: float | None = None) -> IntegrationTestResult:
    return IntegrationTestResult(
        ok=False,
        message=message,
        latency_ms=int((time.monotonic() - started) * 1000)
        if started is not None
        else None,
    )


def _g(cfg: dict, *path: str, default: Any = None) -> Any:  # noqa: ANN401
    node: Any = cfg
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# --- SMTP -----------------------------------------------------------------------------
def test_smtp(cfg: dict) -> IntegrationTestResult:
    host = _g(cfg, "smtp_host") or ""
    port = int(_g(cfg, "smtp_port") or 587)
    user = _g(cfg, "smtp_user") or ""
    password = _g(cfg, "smtp_password") or ""
    use_tls = bool(_g(cfg, "use_tls"))
    if not host:
        return _err("smtp_host is required")
    started = time.monotonic()
    try:
        if use_tls and port == 465:
            client = smtplib.SMTP_SSL(host, port, timeout=DEFAULT_TIMEOUT)
        else:
            client = smtplib.SMTP(host, port, timeout=DEFAULT_TIMEOUT)
            if use_tls:
                client.starttls(context=ssl.create_default_context())
        try:
            if user and password:
                client.login(user, password)
                return _ok(f"Authenticated as {user}", started)
            client.noop()
            return _ok("Connected (no auth provided)", started)
        finally:
            try:
                client.quit()
            except Exception:  # noqa: S110
                # Quit is best-effort; the test result has already been computed.
                pass
    except (TimeoutError, smtplib.SMTPException, OSError) as exc:
        return _err(f"SMTP error: {exc}", started)


# --- Torrent clients ------------------------------------------------------------------
def test_qbittorrent(cfg: dict) -> IntegrationTestResult:
    host = _g(cfg, "host") or "localhost"
    port = int(_g(cfg, "port") or 8080)
    https = bool(_g(cfg, "https"))
    user = _g(cfg, "username") or ""
    password = _g(cfg, "password") or ""
    base = f"{'https' if https else 'http'}://{host}:{port}"
    started = time.monotonic()
    try:
        s = requests.Session()
        login = s.post(
            urljoin(base + "/", "api/v2/auth/login"),
            data={"username": user, "password": password},
            timeout=DEFAULT_TIMEOUT,
            verify=False,
        )
        if login.status_code == 200 and login.text.strip() == "Ok.":
            ver = s.get(
                urljoin(base + "/", "api/v2/app/version"),
                timeout=DEFAULT_TIMEOUT,
                verify=False,
            )
            return _ok(f"qBittorrent {ver.text.strip()}", started)
        return _err(
            f"Login failed (HTTP {login.status_code}: {login.text.strip() or 'no body'})",
            started,
        )
    except requests.RequestException as exc:
        return _err(f"qBittorrent error: {exc}", started)


def test_transmission(cfg: dict) -> IntegrationTestResult:
    host = _g(cfg, "host") or "localhost"
    port = int(_g(cfg, "port") or 9091)
    https = bool(_g(cfg, "https"))
    user = _g(cfg, "username") or ""
    password = _g(cfg, "password") or ""
    rpc_path = _g(cfg, "path") or "/transmission/rpc"
    base = f"{'https' if https else 'http'}://{host}:{port}{rpc_path}"
    started = time.monotonic()
    auth = (user, password) if user else None
    try:
        # First call: get session id from 409 response, retry with header
        r1 = requests.post(
            base,
            json={"method": "session-get"},
            timeout=DEFAULT_TIMEOUT,
            auth=auth,
            verify=False,  # noqa: S501
        )
        session_id = r1.headers.get("X-Transmission-Session-Id")
        if r1.status_code == 401:
            return _err("Authentication failed (HTTP 401)", started)
        if not session_id and r1.status_code != 200:
            return _err(f"Unexpected response (HTTP {r1.status_code})", started)
        r2 = requests.post(
            base,
            json={"method": "session-get"},
            headers={"X-Transmission-Session-Id": session_id} if session_id else {},
            timeout=DEFAULT_TIMEOUT,
            auth=auth,
            verify=False,  # noqa: S501
        )
        if r2.status_code != 200:
            return _err(f"Unexpected response (HTTP {r2.status_code})", started)
        version = r2.json().get("arguments", {}).get("version", "?")
        return _ok(f"Transmission {version}", started)
    except requests.RequestException as exc:
        return _err(f"Transmission error: {exc}", started)


def test_sabnzbd(cfg: dict) -> IntegrationTestResult:
    host = _g(cfg, "host") or "localhost"
    port = int(_g(cfg, "port") or 8080)
    https = bool(_g(cfg, "https"))
    api_key = _g(cfg, "api_key") or ""
    base_path = _g(cfg, "base_path") or ""
    base = f"{'https' if https else 'http'}://{host}:{port}{base_path}"
    started = time.monotonic()
    try:
        r = requests.get(
            urljoin(base + "/", "api"),
            params={"mode": "version", "output": "json", "apikey": api_key},
            timeout=DEFAULT_TIMEOUT,
            verify=False,  # noqa: S501
        )
        if r.status_code != 200:
            return _err(f"HTTP {r.status_code}", started)
        data = r.json()
        if isinstance(data, dict) and "version" in data:
            return _ok(f"SABnzbd {data['version']}", started)
        if isinstance(data, dict) and data.get("status") is False:
            return _err(f"SABnzbd: {data.get('error', 'unknown error')}", started)
        return _err("Unexpected SABnzbd response", started)
    except requests.RequestException as exc:
        return _err(f"SABnzbd error: {exc}", started)


# --- Metadata providers ---------------------------------------------------------------
def test_tmdb(cfg: dict) -> IntegrationTestResult:
    api_key = _g(cfg, "api_key") or ""
    if not api_key:
        return _err("api_key is required")
    started = time.monotonic()
    try:
        r = requests.get(
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return _ok("TMDB API key valid", started)
        if r.status_code == 401:
            return _err("Invalid TMDB API key (401)", started)
        return _err(f"TMDB returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        return _err(f"TMDB error: {exc}", started)


def test_tvdb(cfg: dict) -> IntegrationTestResult:
    api_key = _g(cfg, "api_key") or ""
    if not api_key:
        return _err("api_key is required")
    started = time.monotonic()
    try:
        r = requests.post(
            "https://api4.thetvdb.com/v4/login",
            json={"apikey": api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200 and r.json().get("status") == "success":
            return _ok("TVDB API key valid", started)
        return _err(f"TVDB returned HTTP {r.status_code}: {r.text[:120]}", started)
    except requests.RequestException as exc:
        return _err(f"TVDB error: {exc}", started)


# --- Notification providers -----------------------------------------------------------
def test_gotify(cfg: dict) -> IntegrationTestResult:
    url = (_g(cfg, "url") or "").rstrip("/")
    if not url:
        return _err("url is required")
    started = time.monotonic()
    try:
        r = requests.get(f"{url}/version", timeout=DEFAULT_TIMEOUT)
        if r.status_code == 200:
            try:
                version = r.json().get("version", "?")
            except ValueError:
                version = "?"
            return _ok(f"Gotify {version}", started)
        return _err(f"Gotify returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        return _err(f"Gotify error: {exc}", started)


def test_ntfy(cfg: dict) -> IntegrationTestResult:
    url = (_g(cfg, "url") or "").rstrip("/")
    if not url:
        return _err("url is required")
    started = time.monotonic()
    try:
        # Use a HEAD request against the topic URL; ntfy returns 200 for valid topics.
        r = requests.head(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
        if r.status_code in (
            200,
            405,
        ):  # some servers reject HEAD with 405 but topic exists
            return _ok("Reachable", started)
        return _err(f"ntfy returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        return _err(f"ntfy error: {exc}", started)


def test_pushover(cfg: dict) -> IntegrationTestResult:
    api_key = _g(cfg, "api_key") or ""
    user = _g(cfg, "user") or ""
    if not api_key or not user:
        return _err("api_key and user are required")
    started = time.monotonic()
    try:
        r = requests.post(
            "https://api.pushover.net/1/users/validate.json",
            data={"token": api_key, "user": user},
            timeout=DEFAULT_TIMEOUT,
        )
        data = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if r.status_code == 200 and data.get("status") == 1:
            return _ok("Pushover credentials valid", started)
        errors = data.get("errors") or [f"HTTP {r.status_code}"]
        return _err(f"Pushover: {'; '.join(errors)}", started)
    except requests.RequestException as exc:
        return _err(f"Pushover error: {exc}", started)


# --- Subtitles / Requests / OIDC ------------------------------------------------------
def test_bazarr(cfg: dict) -> IntegrationTestResult:
    url = (_g(cfg, "url") or "").rstrip("/")
    api_key = _g(cfg, "api_key") or ""
    if not url:
        return _err("url is required")
    started = time.monotonic()
    try:
        r = requests.get(
            f"{url}/api/system/status",
            headers={"X-API-KEY": api_key} if api_key else {},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return _ok("Bazarr reachable", started)
        if r.status_code == 401:
            return _err("Invalid Bazarr API key (401)", started)
        return _err(f"Bazarr returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        return _err(f"Bazarr error: {exc}", started)


def test_seerr(cfg: dict) -> IntegrationTestResult:
    url = (_g(cfg, "url") or "").rstrip("/")
    api_key = _g(cfg, "api_key") or ""
    if not url:
        return _err("url is required")
    started = time.monotonic()
    try:
        r = requests.get(
            f"{url}/api/v1/status",
            headers={"X-Api-Key": api_key} if api_key else {},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            try:
                version = r.json().get("version", "?")
            except ValueError:
                version = "?"
            return _ok(f"Overseerr/Jellyseerr {version}", started)
        if r.status_code in (401, 403):
            return _err("Invalid API key", started)
        return _err(f"Returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        return _err(f"Seerr error: {exc}", started)


def test_oidc(cfg: dict) -> IntegrationTestResult:
    endpoint = _g(cfg, "configuration_endpoint") or ""
    if not endpoint:
        return _err("configuration_endpoint is required")
    started = time.monotonic()
    try:
        r = requests.get(endpoint, timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            return _err(f"Discovery returned HTTP {r.status_code}", started)
        data = r.json()
        if "authorization_endpoint" in data and "token_endpoint" in data:
            return _ok(f"OIDC issuer: {data.get('issuer', endpoint)}", started)
        return _err("Discovery document missing required endpoints", started)
    except requests.RequestException as exc:
        return _err(f"OIDC error: {exc}", started)
    except ValueError as exc:
        return _err(f"OIDC discovery JSON invalid: {exc}", started)


HANDLERS = {
    "smtp": test_smtp,
    "qbittorrent": test_qbittorrent,
    "transmission": test_transmission,
    "sabnzbd": test_sabnzbd,
    "tmdb": test_tmdb,
    "tvdb": test_tvdb,
    "bazarr": test_bazarr,
    "gotify": test_gotify,
    "ntfy": test_ntfy,
    "pushover": test_pushover,
    "seerr": test_seerr,
    "oidc": test_oidc,
}

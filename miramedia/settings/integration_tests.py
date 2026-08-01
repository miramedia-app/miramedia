"""Connection/auth test handlers for the settings page integrations.

Each handler receives a typed config pulled from the live form state and returns an
``IntegrationTestResult``. Tests must be best-effort and fail closed — the goal is to give
operators a quick "yes/no" without persisting anything. Avoid long timeouts so a single
broken integration can't block the UI.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from typing import Annotated, Any
from urllib.parse import urljoin

import requests
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8.0

HostStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$")]
PathStr = Annotated[str, Field(pattern=r"^$|^/[^\s]*$")]


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


def _format_config_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        if loc:
            parts.append(f"{loc}: {msg}")
        else:
            parts.append(str(msg))
    return "; ".join(parts)


class SmtpTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    use_tls: bool = False


class _HostPortConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: HostStr = "localhost"
    port: int = Field(ge=1, le=65535)
    https: bool = False


class QbittorrentTestConfig(_HostPortConfig):
    port: int = Field(default=8080, ge=1, le=65535)
    username: str = ""
    password: str = ""
    allow_self_signed: bool = False


class TransmissionTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: HostStr = "localhost"
    port: int = Field(default=9091, ge=1, le=65535)
    https_enabled: bool = False
    username: str = ""
    password: str = ""
    path: PathStr = "/transmission/rpc"
    allow_self_signed: bool = False


class SabnzbdTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: HostStr = "localhost"
    port: int = Field(default=8080, ge=1, le=65535)
    https: bool = False
    api_key: str = ""
    base_path: PathStr = ""
    allow_self_signed: bool = False


class ApiKeyTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""


class UrlTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost/"))

    @field_validator("url", mode="before")
    @classmethod
    def _default_empty_url(cls, value: object) -> object:
        if value in ("", None):
            return "http://localhost/"
        return value


class BazarrTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost/"))
    api_key: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _default_empty_url(cls, value: object) -> object:
        if value in ("", None):
            return "http://localhost/"
        return value


class PushoverTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""
    user: str = ""


class SeerrTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost/"))
    api_key: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _default_empty_url(cls, value: object) -> object:
        if value in ("", None):
            return "http://localhost/"
        return value


class OidcTestConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    configuration_endpoint: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost/"))

    @field_validator("configuration_endpoint", mode="before")
    @classmethod
    def _default_empty_endpoint(cls, value: object) -> object:
        if value in ("", None):
            return "http://localhost/"
        return value


def _tls_verify(
    cfg: QbittorrentTestConfig | TransmissionTestConfig | SabnzbdTestConfig,
) -> bool:
    return not cfg.allow_self_signed


def _request_tls_error(started: float) -> IntegrationTestResult:
    return _err(
        "TLS certificate verification failed — enable 'Allow self-signed certificate' "
        "if this server uses one",
        started,
    )


def _scrub_secret(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _request_failure_message(service: str, exc: BaseException) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{service}: connection failed (timeout or unreachable host)"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"{service}: connection failed (timeout or unreachable host)"
    if isinstance(exc, requests.exceptions.RequestException):
        return f"{service}: connection failed"
    return f"{service}: connection failed"


def _log_request_exception(
    service: str,
    exc: BaseException,
    *,
    secret: str = "",
) -> None:
    log.debug(
        "%s request failed: %s",
        service,
        _scrub_secret(str(exc), secret),
        exc_info=(type(exc), exc, exc.__traceback__),
    )


# --- SMTP -----------------------------------------------------------------------------
def test_smtp(cfg: SmtpTestConfig) -> IntegrationTestResult:
    if not cfg.smtp_host:
        return _err("smtp_host is required")
    started = time.monotonic()
    try:
        if cfg.use_tls and cfg.smtp_port == 465:
            client = smtplib.SMTP_SSL(
                cfg.smtp_host, cfg.smtp_port, timeout=DEFAULT_TIMEOUT
            )
        else:
            client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=DEFAULT_TIMEOUT)
            if cfg.use_tls:
                client.starttls(context=ssl.create_default_context())
        try:
            if cfg.smtp_user and cfg.smtp_password:
                client.login(cfg.smtp_user, cfg.smtp_password)
                return _ok(f"Authenticated as {cfg.smtp_user}", started)
            client.noop()
            return _ok("Connected (no auth provided)", started)
        finally:
            try:
                client.quit()
            except Exception:  # noqa: S110
                pass
    except TimeoutError:
        log.debug("SMTP connection failed", exc_info=True)
        return _err("SMTP: connection failed (timeout)", started)
    except (smtplib.SMTPException, OSError):
        log.debug("SMTP connection failed", exc_info=True)
        return _err("SMTP: connection failed", started)


# --- Torrent clients ------------------------------------------------------------------
def test_qbittorrent(cfg: QbittorrentTestConfig) -> IntegrationTestResult:
    base = f"{'https' if cfg.https else 'http'}://{cfg.host}:{cfg.port}"
    verify = _tls_verify(cfg)
    started = time.monotonic()
    try:
        s = requests.Session()
        login = s.post(
            urljoin(base + "/", "api/v2/auth/login"),
            data={"username": cfg.username, "password": cfg.password},
            timeout=DEFAULT_TIMEOUT,
            verify=verify,
        )
        if login.status_code == 200 and login.text.strip() == "Ok.":
            ver = s.get(
                urljoin(base + "/", "api/v2/app/version"),
                timeout=DEFAULT_TIMEOUT,
                verify=verify,
            )
            return _ok(f"qBittorrent {ver.text.strip()}", started)
        return _err(
            f"Login failed (HTTP {login.status_code}: {login.text.strip() or 'no body'})",
            started,
        )
    except requests.exceptions.SSLError:
        return _request_tls_error(started)
    except requests.RequestException as exc:
        _log_request_exception("qBittorrent", exc, secret=cfg.password)
        return _err(_request_failure_message("qBittorrent", exc), started)


def test_transmission(cfg: TransmissionTestConfig) -> IntegrationTestResult:
    base = (
        f"{'https' if cfg.https_enabled else 'http'}://{cfg.host}:{cfg.port}{cfg.path}"
    )
    verify = _tls_verify(cfg)
    started = time.monotonic()
    auth = (cfg.username, cfg.password) if cfg.username else None
    try:
        r1 = requests.post(
            base,
            json={"method": "session-get"},
            timeout=DEFAULT_TIMEOUT,
            auth=auth,
            verify=verify,
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
            verify=verify,
        )
        if r2.status_code != 200:
            return _err(f"Unexpected response (HTTP {r2.status_code})", started)
        version = r2.json().get("arguments", {}).get("version", "?")
        return _ok(f"Transmission {version}", started)
    except requests.exceptions.SSLError:
        return _request_tls_error(started)
    except requests.RequestException as exc:
        _log_request_exception("Transmission", exc)
        return _err(_request_failure_message("Transmission", exc), started)


def test_sabnzbd(cfg: SabnzbdTestConfig) -> IntegrationTestResult:
    base = f"{'https' if cfg.https else 'http'}://{cfg.host}:{cfg.port}{cfg.base_path}"
    verify = _tls_verify(cfg)
    started = time.monotonic()
    try:
        r = requests.get(
            urljoin(base + "/", "api"),
            params={"mode": "version", "output": "json", "apikey": cfg.api_key},
            timeout=DEFAULT_TIMEOUT,
            verify=verify,
        )
        if r.status_code != 200:
            return _err(f"HTTP {r.status_code}", started)
        data = r.json()
        if isinstance(data, dict) and "version" in data:
            return _ok(f"SABnzbd {data['version']}", started)
        if isinstance(data, dict) and data.get("status") is False:
            return _err(f"SABnzbd: {data.get('error', 'unknown error')}", started)
        return _err("Unexpected SABnzbd response", started)
    except requests.exceptions.SSLError:
        return _request_tls_error(started)
    except requests.RequestException as exc:
        _log_request_exception("SABnzbd", exc, secret=cfg.api_key)
        return _err(_request_failure_message("SABnzbd", exc), started)


# --- Metadata providers ---------------------------------------------------------------
def test_tmdb(cfg: ApiKeyTestConfig) -> IntegrationTestResult:
    if not cfg.api_key:
        return _err("api_key is required")
    started = time.monotonic()
    try:
        r = requests.get(
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": cfg.api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return _ok("TMDB API key valid", started)
        if r.status_code == 401:
            return _err("Invalid TMDB API key (401)", started)
        return _err(f"TMDB returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        _log_request_exception("TMDB", exc, secret=cfg.api_key)
        return _err(_request_failure_message("TMDB", exc), started)


def test_tvdb(cfg: ApiKeyTestConfig) -> IntegrationTestResult:
    if not cfg.api_key:
        return _err("api_key is required")
    started = time.monotonic()
    try:
        r = requests.post(
            "https://api4.thetvdb.com/v4/login",
            json={"apikey": cfg.api_key},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200 and r.json().get("status") == "success":
            return _ok("TVDB API key valid", started)
        return _err(f"TVDB returned HTTP {r.status_code}: {r.text[:120]}", started)
    except requests.RequestException as exc:
        _log_request_exception("TVDB", exc, secret=cfg.api_key)
        return _err(_request_failure_message("TVDB", exc), started)


# --- Notification providers -----------------------------------------------------------
def test_gotify(cfg: UrlTestConfig) -> IntegrationTestResult:
    url = str(cfg.url).rstrip("/")
    if not url or url == "http://localhost":
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
        _log_request_exception("Gotify", exc)
        return _err(_request_failure_message("Gotify", exc), started)


def test_ntfy(cfg: UrlTestConfig) -> IntegrationTestResult:
    url = str(cfg.url).rstrip("/")
    if not url or url == "http://localhost":
        return _err("url is required")
    started = time.monotonic()
    try:
        r = requests.head(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
        if r.status_code in (200, 405):
            return _ok("Reachable", started)
        return _err(f"ntfy returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        _log_request_exception("ntfy", exc)
        return _err(_request_failure_message("ntfy", exc), started)


def test_pushover(cfg: PushoverTestConfig) -> IntegrationTestResult:
    if not cfg.api_key or not cfg.user:
        return _err("api_key and user are required")
    started = time.monotonic()
    try:
        r = requests.post(
            "https://api.pushover.net/1/users/validate.json",
            data={"token": cfg.api_key, "user": cfg.user},
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
        _log_request_exception("Pushover", exc, secret=cfg.api_key)
        return _err(_request_failure_message("Pushover", exc), started)


# --- Subtitles / Requests / OIDC ------------------------------------------------------
def test_bazarr(cfg: BazarrTestConfig) -> IntegrationTestResult:
    url = str(cfg.url).rstrip("/")
    if not url or url == "http://localhost":
        return _err("url is required")
    started = time.monotonic()
    try:
        r = requests.get(
            f"{url}/api/system/status",
            headers={"X-API-KEY": cfg.api_key} if cfg.api_key else {},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return _ok("Bazarr reachable", started)
        if r.status_code == 401:
            return _err("Invalid Bazarr API key (401)", started)
        return _err(f"Bazarr returned HTTP {r.status_code}", started)
    except requests.RequestException as exc:
        _log_request_exception("Bazarr", exc, secret=cfg.api_key)
        return _err(_request_failure_message("Bazarr", exc), started)


def test_seerr(cfg: SeerrTestConfig) -> IntegrationTestResult:
    url = str(cfg.url).rstrip("/")
    if not url or url == "http://localhost":
        return _err("url is required")
    started = time.monotonic()
    try:
        r = requests.get(
            f"{url}/api/v1/status",
            headers={"X-Api-Key": cfg.api_key} if cfg.api_key else {},
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
        _log_request_exception("Seerr", exc, secret=cfg.api_key)
        return _err(_request_failure_message("Seerr", exc), started)


def test_oidc(cfg: OidcTestConfig) -> IntegrationTestResult:
    endpoint = str(cfg.configuration_endpoint)
    if not endpoint or endpoint == "http://localhost/":
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
        _log_request_exception("OIDC", exc)
        return _err(_request_failure_message("OIDC", exc), started)
    except ValueError:
        log.debug("OIDC discovery JSON invalid", exc_info=True)
        return _err("OIDC discovery JSON invalid", started)


INTEGRATION_EFFECTIVE_PATHS: dict[str, tuple[str, ...]] = {
    "smtp": ("notifications", "smtp_config"),
    "qbittorrent": ("torrents", "qbittorrent"),
    "transmission": ("torrents", "transmission"),
    "sabnzbd": ("torrents", "sabnzbd"),
    "tmdb": ("metadata", "tmdb"),
    "tvdb": ("metadata", "tvdb"),
    "bazarr": ("subtitles", "bazarr"),
    "gotify": ("notifications", "gotify"),
    "ntfy": ("notifications", "ntfy"),
    "pushover": ("notifications", "pushover"),
    "seerr": ("requests", "seerr"),
    "oidc": ("auth", "openid_connect"),
}

HANDLERS: dict[str, tuple[type[BaseModel], Any]] = {
    "smtp": (SmtpTestConfig, test_smtp),
    "qbittorrent": (QbittorrentTestConfig, test_qbittorrent),
    "transmission": (TransmissionTestConfig, test_transmission),
    "sabnzbd": (SabnzbdTestConfig, test_sabnzbd),
    "tmdb": (ApiKeyTestConfig, test_tmdb),
    "tvdb": (ApiKeyTestConfig, test_tvdb),
    "bazarr": (BazarrTestConfig, test_bazarr),
    "gotify": (UrlTestConfig, test_gotify),
    "ntfy": (UrlTestConfig, test_ntfy),
    "pushover": (PushoverTestConfig, test_pushover),
    "seerr": (SeerrTestConfig, test_seerr),
    "oidc": (OidcTestConfig, test_oidc),
}

for _handler in (entry[1] for entry in HANDLERS.values()):
    _handler.__test__ = False  # not pytest tests; API handlers only

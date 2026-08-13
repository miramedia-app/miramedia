"""Baseline browser security response headers for API responses."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from miramedia.auth.users import _cookie_secure
from miramedia.config import MiraMediaConfig

_BASELINE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

_HSTS_VALUE = "max-age=31536000; includeSubDomains"

_CSP_VALUE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware:
    """Attach defense-in-depth headers on http.response.start — no body re-pump."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _BASELINE_HEADERS.items():
                    headers[name] = value
                if _cookie_secure():
                    headers["Strict-Transport-Security"] = _HSTS_VALUE
                misc = MiraMediaConfig().misc
                if misc.csp_enabled:
                    header_name = (
                        "Content-Security-Policy"
                        if misc.csp_enforce
                        else "Content-Security-Policy-Report-Only"
                    )
                    headers[header_name] = _CSP_VALUE
            await send(message)

        await self.app(scope, receive, send_with_headers)

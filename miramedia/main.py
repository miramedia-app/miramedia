import importlib.metadata
import logging
import mimetypes
import os
import time
import tomllib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.datastructures import Headers
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import FileResponse, RedirectResponse
from starlette.routing import Match, Mount
from starlette.staticfiles import NotModifiedResponse
from starlette.types import Scope
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import miramedia.imports.router as imports_router
import miramedia.movies.router as movies_router
import miramedia.shows.router as shows_router
import miramedia.torrents.router as torrents_router
from miramedia.auth.router import (
    auth_metadata_router,
    get_openid_router,
)
from miramedia.auth.router import (
    users_router as custom_users_router,
)
from miramedia.auth.runtime import OAuthRuntimeMiddleware
from miramedia.auth.schemas import UserCreate, UserRead, UserUpdate
from miramedia.auth.users import (
    bearer_auth_backend,
    cookie_auth_backend,
    current_superuser,
    fastapi_users,
)
from miramedia.config import MiraMediaConfig
from miramedia.exceptions import register_exception_handlers
from miramedia.filesystem_checks import run_filesystem_checks
from miramedia.logging import LOGGING_CONFIG, setup_logging
from miramedia.notifications.router import router as notification_router

setup_logging()

config = MiraMediaConfig()
log = logging.getLogger(__name__)

if config.misc.development:
    log.warning("Development Mode activated!")

run_filesystem_checks(config, log)

BASE_PATH = os.getenv("BASE_PATH", "")
FRONTEND_FILES_DIR = os.getenv("FRONTEND_FILES_DIR")
DISABLE_FRONTEND_MOUNT = (
    os.getenv("MIRAMEDIA_DISABLE_FRONTEND_MOUNT", "").lower() == "true"
)
FRONTEND_FOLLOW_SYMLINKS = os.getenv("FRONTEND_FOLLOW_SYMLINKS", "").lower() == "true"

log.info("Hello World!")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    from miramedia.startup import (
        SchedulerContext,
        acquire_scheduler_leadership,
        configure_threadpool,
        is_scheduler_disabled,
        schedule_import_queue_warmup,
        shutdown_startup,
        start_library_watcher,
        start_native_torrent_client,
        start_persistence,
        start_scheduler_workers,
        warm_cloudflare_bypass,
    )

    scheduler_ctx = SchedulerContext()
    native_client = None
    try:
        configure_threadpool()
        await start_persistence()
        start_library_watcher()
        schedule_import_queue_warmup()
        native_client = await start_native_torrent_client()
        warm_cloudflare_bypass()

        if is_scheduler_disabled():
            log.info(
                "MIRAMEDIA_SCHEDULER_DISABLED=true — taskiq brokers, receivers, "
                "and scheduler loop skipped on this worker"
            )
            yield
        else:
            (
                scheduler_leader_conn,
                is_scheduler_leader,
            ) = await acquire_scheduler_leadership()
            scheduler_ctx.scheduler_leader_conn = scheduler_leader_conn
            if not is_scheduler_leader:
                log.info(
                    "Another worker owns the scheduler advisory lock; "
                    "serving API only in this worker"
                )
                yield
                return
            await start_scheduler_workers(app, scheduler_ctx)
            yield
    finally:
        await shutdown_startup(scheduler_ctx, native_client)


# Swagger UI / ReDoc are replaced by the embedded Scalar API reference in the
# docs site (frontend /docs/api-reference). The backend only exposes the raw
# OpenAPI schema at /openapi.json.
def _resolve_app_version() -> str:
    """Version shown in the OpenAPI spec / docs API reference.

    Prefer ``PUBLIC_VERSION`` (injected at image build from the release tag,
    ``dev`` in the dev stack) so the docs match the running build. Fall back to
    the installed package metadata so a bare ``uvicorn`` run still reports the
    real version instead of FastAPI's ``0.1.0`` default.
    """
    env_version = os.getenv("PUBLIC_VERSION")
    if env_version:
        return env_version
    try:
        return importlib.metadata.version("miramedia")
    except importlib.metadata.PackageNotFoundError:
        pass
    # Source checkout (not pip-installed): read the version straight from
    # pyproject.toml sitting one level above this package.
    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return data["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0"


app = FastAPI(
    root_path=BASE_PATH,
    lifespan=lifespan,
    title="MiraMedia",
    version=_resolve_app_version(),
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(OAuthRuntimeMiddleware)


@app.middleware("http")
async def server_timing_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
    return response


@app.middleware("http")
async def api_trailing_slash_redirect_middleware(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Make the trailing-slash variant of API collection-root endpoints resolve.

    Collection roots are declared ``@router.get("")`` so the canonical path is
    the *no-slash* form (e.g. ``GET /api/v1/shows``); the OpenAPI spec and the
    generated frontend client both use that form. Starlette ships
    ``redirect_slashes=True`` which would normally 307 ``/api/v1/shows/`` →
    ``/api/v1/shows``, but that fallback only runs when *no* route matches the
    incoming path. The SPA is served from a catch-all ``app.mount("/")``: a
    ``Mount`` at ``/`` returns ``Match.FULL`` for *every* path, so the
    trailing-slash request is handed straight to ``StaticFiles`` (which 404s on
    the missing file) before Starlette ever reaches its slash-redirect logic.

    Rather than touch dozens of route decorators (which would change the OpenAPI
    paths / the generated ``api.d.ts``), we restore the missing behaviour in one
    place: for any ``/api/`` path ending in ``/``, strip the slash and, *only if*
    the stripped path matches a real registered route, emit a 307 to the
    canonical no-slash form. Paths that don't map to a route (genuine 404s,
    unknown API paths) fall through untouched, so existing 404 behaviour and the
    SPA fallback are unaffected.
    """
    path = request.url.path
    if (
        path.startswith("/api/")
        and path.endswith("/")
        and path != "/api/"
        and path != "/api//"
    ):
        stripped = path.rstrip("/")
        # Probe the registered routes against the stripped path using a shallow
        # scope copy. Only redirect when something actually handles the no-slash
        # form, so genuine unknown paths keep 404ing.
        probe_scope = dict(request.scope)
        probe_scope["path"] = stripped
        for route in app.router.routes:
            match, _ = route.matches(probe_scope)
            # A bare ``Mount("/")`` matches everything; skip it so we only react
            # to real API routes, not the SPA catch-all.
            if match != Match.NONE and not (
                isinstance(route, Mount) and route.path == ""
            ):
                target = stripped
                if request.url.query:
                    target = f"{target}?{request.url.query}"
                return RedirectResponse(url=target, status_code=307)
    return await call_next(request)


# Middleware order: Starlette evaluates the LAST `add_middleware` call as the
# innermost layer (closest to the app), and the FIRST call as the outermost.
# Effective request flow (outermost → innermost):
#   1. ProxyHeadersMiddleware — needs the raw client IP from forwarded headers
#      before anything else touches the scope.
#   2. CORSMiddleware — answer preflight OPTIONS before correlation tagging,
#      otherwise short-circuited preflights skip our id header anyway.
#   3. GZipMiddleware — compress responses (HTML, JSON, JS, CSS, fonts). The
#      ``minimum_size=1000`` threshold skips small payloads where compression
#      adds CPU + framing overhead but no meaningful size win.
#   4. CorrelationIdMiddleware — innermost so every response (including
#      handler-raised errors) carries an X-Correlation-ID.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
origins = config.misc.cors_urls
log.info(f"CORS URLs activated for following origins: {origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    # Without this, cross-origin JS cannot read X-Total-Count and paginated
    # lists collapse to a single page.
    expose_headers=["X-Total-Count"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CorrelationIdMiddleware, header_name="X-Correlation-ID")
api_app = APIRouter(prefix="/api/v1")

from miramedia.core.router import router as core_router  # noqa: E402

api_app.include_router(core_router)

api_app.include_router(
    fastapi_users.get_auth_router(bearer_auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
api_app.include_router(
    fastapi_users.get_auth_router(cookie_auth_backend),
    prefix="/auth/cookie",
    tags=["auth"],
)
api_app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
api_app.include_router(
    fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"]
)
api_app.include_router(
    fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"]
)
api_app.include_router(custom_users_router)
api_app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)
api_app.include_router(auth_metadata_router)
api_app.include_router(get_openid_router(), tags=["openid"], prefix="/auth/oauth")

api_app.include_router(shows_router.router)
api_app.include_router(shows_router.episodes_router)
api_app.include_router(shows_router.seasons_router)
api_app.include_router(torrents_router.router)
api_app.include_router(movies_router.router)
api_app.include_router(imports_router.router)
api_app.include_router(notification_router)

# SSE event stream — server-push channel that replaces dashboard polling
# loops on /dashboard/torrents and /dashboard/imports. Clients receive
# small ``{id}`` notifications and re-fetch via the REST endpoints they
# already use, so this stays additive to the existing API surface.
# The router imports below are deferred to avoid circular import / startup
# ordering issues — hence the E402 suppressions on each.
from miramedia.events.router import router as events_router  # noqa: E402

api_app.include_router(events_router)

from miramedia.indexers.router import router as indexers_router  # noqa: E402
from miramedia.logs.router import router as logs_router  # noqa: E402
from miramedia.settings.router import router as settings_router  # noqa: E402
from miramedia.updates.router import router as updates_router  # noqa: E402

api_app.include_router(indexers_router)
api_app.include_router(logs_router)
api_app.include_router(settings_router)
api_app.include_router(updates_router)

from miramedia.streams.router import router as streams_router  # noqa: E402
from miramedia.subtitles.router import router as subtitles_router  # noqa: E402

api_app.include_router(streams_router)
api_app.include_router(subtitles_router)

# Mount requests router unconditionally so types appear in the OpenAPI
# spec; the router itself enforces the runtime ``requests.enabled`` flag
# via ``require_requests_enabled``. (import deferred: circular-import / ordering)
from miramedia.requests.router import router as requests_router  # noqa: E402

api_app.include_router(requests_router)
if config.requests.enabled:
    log.info("Requests feature enabled")

# Web Vitals beacon endpoint (POST /api/v1/analytics/vitals). Hidden from
# the OpenAPI schema by the router itself — it's an operational sink for
# the frontend, not a public API. The full Prometheus surface is exposed
# at /api/v1/metrics below. (import deferred: circular-import / ordering)
from miramedia.observability.router import router as observability_router  # noqa: E402

api_app.include_router(observability_router)
from miramedia.ops.router import router as ops_router  # noqa: E402

api_app.include_router(ops_router)

# Prometheus metrics. The instrumentator hooks into the parent ``app``
# (it needs the live ASGI middleware stack to time requests) and exposes
# the scrape endpoint on the same ``app`` — its ``.expose()`` helper calls
# ``add_route(path, route, include_in_schema=...)`` which only works on
# ``FastAPI``/``Starlette`` apps, not an ``APIRouter``. We still want the
# scrape URL under the ``/api/v1`` prefix so reverse-proxy ACLs that gate
# the API path also gate metrics, so the path is hard-coded here.
# Healthcheck + SSE stream are excluded — they're hit every second and
# would dominate the histogram with noise. include_in_schema=False keeps
# /metrics out of the OpenAPI spec (and therefore the Scalar docs).
# Default: deny — only superusers may scrape. Set misc.metrics_public=true
# to allow unauthenticated access (useful when Prometheus has no credentials
# and the endpoint is firewalled from end-users).
_metrics_kwargs: dict[str, Any] = {}
if not config.misc.metrics_public:
    _metrics_kwargs["dependencies"] = [Depends(current_superuser)]
Instrumentator(
    excluded_handlers=["/api/v1/health", "/api/v1/metrics", "/api/v1/events/stream"],
    should_group_status_codes=True,
).instrument(app).expose(
    app, endpoint="/api/v1/metrics", include_in_schema=False, **_metrics_kwargs
)

app.include_router(api_app)


# Serve the SPA shell for unmatched frontend routes. Next.js static export
# emits per-route HTML files at known paths; dynamic routes (UUID segments)
# emit a single `_shell/index.html` that the client router resolves via
# `useParams()`. The 404 handler rewrites UUID paths to that shell so the
# backend doesn't have to know about every show/movie ID.
import re  # noqa: E402 — deferred to avoid circular import / startup ordering

_UUID_RE = re.compile(
    r"^/dashboard/(shows|movies)/[0-9a-fA-F-]{8,}(?:/[0-9a-fA-F-]{8,})?/?$"
)


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc: Exception) -> Response:
    if DISABLE_FRONTEND_MOUNT:
        return Response(content="Not Found", status_code=404)
    path = request.url.path
    # Rewrite UUID detail paths to the dynamic-route shell index.html
    match = _UUID_RE.match(path)
    if match:
        media_type = match.group(1)
        # Count UUIDs to decide season vs show shell
        uuid_count = len(
            re.findall(r"[0-9a-fA-F-]{8,}", path[len(f"/dashboard/{media_type}/") :])
        )
        if media_type == "shows" and uuid_count >= 2:
            shell = f"{FRONTEND_FILES_DIR}/dashboard/shows/_shell/_shell/index.html"
        else:
            shell = f"{FRONTEND_FILES_DIR}/dashboard/{media_type}/_shell/index.html"
        if Path(shell).is_file():  # noqa: ASYNC240 — cheap stat, intentional
            return FileResponse(shell)
    # Generic SPA fallback for anything that isn't an API route
    if not path.startswith("/api/"):
        fallback = f"{FRONTEND_FILES_DIR}/index.html"
        if Path(fallback).is_file():  # noqa: ASYNC240 — cheap stat, intentional
            return FileResponse(fallback)
    return Response(content="Not Found", status_code=404)


# Static frontend mounted at root LAST so explicit /api/* routes win route
# resolution. Without this ordering the mount would shadow API endpoints.
class CachedStaticFiles(StaticFiles):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        # Memoize which precompressed variants (.br / .gz) exist per asset so we
        # stat the filesystem at most once per path instead of on every request.
        # Frontend bundles are immutable for the process lifetime, so a stale
        # entry is not a concern.
        self._precompressed: dict[str, dict[str, Path]] = {}

    def _variants_for(self, path: str) -> dict[str, Path]:
        cached = self._precompressed.get(path)
        if cached is None:
            cached = {}
            br = Path(self.directory) / f"{path}.br"
            if br.is_file():
                cached["br"] = br
            gz = Path(self.directory) / f"{path}.gz"
            if gz.is_file():
                cached["gzip"] = gz
            self._precompressed[path] = cached
        return cached

    async def get_response(self, path: str, scope: Scope) -> Response:
        headers = Headers(scope=scope)
        if not path.endswith((".br", ".gz")):
            variants = self._variants_for(path)
            encoding = None
            compressed_path = None
            if "br" in headers.get("accept-encoding", "") and "br" in variants:
                encoding = "br"
                compressed_path = variants["br"]
            if (
                compressed_path is None
                and "gzip" in headers.get("accept-encoding", "")
                and "gzip" in variants
            ):
                encoding = "gzip"
                compressed_path = variants["gzip"]
            if compressed_path is not None and encoding is not None:
                media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                stat_result = await anyio.to_thread.run_sync(os.stat, compressed_path)
                resp = FileResponse(
                    compressed_path, media_type=media_type, stat_result=stat_result
                )
                resp.headers["Content-Encoding"] = encoding
                resp.headers["Vary"] = "Accept-Encoding"
                if path.startswith("_next/static/") or "/_next/static/" in path:
                    resp.headers["Cache-Control"] = (
                        "public, max-age=31536000, immutable"
                    )
                # Preserve StaticFiles' conditional-request behavior (304) that the
                # custom precompressed branch would otherwise bypass.
                if self.is_not_modified(resp.headers, headers):
                    return NotModifiedResponse(resp.headers)
                return resp
        resp = await super().get_response(path, scope)
        # Next.js hashed chunks under /_next/static/ are immutable forever.
        if path.startswith("_next/static/") or "/_next/static/" in path:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.endswith((".woff2", ".woff", ".ico", ".svg")):
            resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp


if not DISABLE_FRONTEND_MOUNT:
    app.mount(
        "/",
        CachedStaticFiles(
            directory=FRONTEND_FILES_DIR,
            html=True,
            follow_symlink=FRONTEND_FOLLOW_SYMLINKS,
        ),
        name="frontend",
    )
    log.debug(f"Mounted frontend at / from {FRONTEND_FILES_DIR}")
else:
    log.info("Frontend mounting disabled (DISABLE_FRONTEND_MOUNT is set)")


register_exception_handlers(app)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=5049,
        log_config=LOGGING_CONFIG,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )

from pydantic import BaseModel
from pydantic_settings import BaseSettings


class RemoteSolverConfig(BaseModel):
    """``solver = "remote"`` — drive a Chrome running on another machine.

    The app keeps using the full native solve loop (nodriver + the Turnstile
    keyboard/mouse flow); it just *connects* to a Chrome you launched on a box
    with a REAL GPU instead of spawning a GPU-less one locally. A real GPU
    means WebGL reports a real renderer string instead of "Google SwiftShader"
    — the single most reliable headless tell — so the browser looks like an
    actual device.

    Launch on the GPU box (your desktop / a spare mini-PC / the docker host),
    NOT headless, NOT --disable-gpu::

        chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cf

    Then point ``endpoint`` at it. From a container, ``host.docker.internal``
    reaches the host; a LAN box is just its IP.
    """

    endpoint: str = (
        ""  # e.g. http://host.docker.internal:9222 or http://192.168.1.50:9222
    )


class FlareSolverrConfig(BaseModel):
    """``solver = "flaresolverr"`` — FlareSolverr sidecar (FlareSolverr/FlareSolverr)."""

    url: str = "http://flaresolverr:8191"


class ByparrConfig(BaseModel):
    """``solver = "byparr"`` — Byparr sidecar (Camoufox, FlareSolverr-compatible API)."""

    url: str = "http://byparr:8191"


class BrowserRunConfig(BaseModel):
    """``solver = "browser_run"`` — Cloudflare Browser Rendering REST API.

    Real Chrome in Cloudflare's own infra. Free tier: 10 min browser time/day,
    3 concurrent. Needs a Cloudflare account id + an API token with the
    Browser Rendering permission.
    """

    account_id: str = ""
    api_token: str = ""


class FirecrawlConfig(BaseModel):
    """``solver = "firecrawl"`` — Firecrawl scrape API (firecrawl.dev)."""

    api_key: str = ""
    base_url: str = "https://api.firecrawl.dev"


# Solver names that DON'T use a local/remote chromium under our control — they
# hand the whole fetch to an external service over HTTP. The bypass skips its
# own browser warmup/launch for these.
EXTERNAL_SOLVERS = frozenset({"byparr", "flaresolverr", "browser_run", "firecrawl"})


class CloudflareConfig(BaseSettings):
    """Top-level Cloudflare-bypass configuration.

    Activates automatically when a request returns a Cloudflare challenge
    response, provided ``enabled`` is True.
    """

    # Master switch. When False the bypass is never invoked: no chromium
    # warmup, no auto-activation on challenge responses, no resources
    # consumed. CF-protected sites simply fail to fetch instead of routing
    # through the browser solver.
    enabled: bool = False
    # Which solver backend earns cf_clearance. All feed the SAME cookie cache +
    # curl_cffi replay machinery; only the "how do we get a fresh clearance"
    # step differs. See the per-backend sub-configs above.
    #   native       — in-process nodriver on this host (default; GPU-less NAS
    #                  reports SwiftShader WebGL → caught everywhere).
    #   remote       — same solve loop, connect to a real-GPU Chrome elsewhere.
    #   byparr       — Camoufox sidecar (highest OSS Turnstile success in 2026).
    #   flaresolverr — classic undetected-chromedriver sidecar.
    #   browser_run  — Cloudflare Browser Rendering (hosted real Chrome, free tier).
    #   firecrawl    — Firecrawl scrape API (free tier: 500 lifetime credits).
    solver: str = "native"
    # Optional residential/mobile proxy applied to the curl_cffi replay AND
    # handed to sidecar solvers (byparr/flaresolverr) for their own egress.
    # Format: scheme://[user:pass@]host:port. Empty = direct.
    proxy: str = ""
    browser_path: str = ""  # auto-detect Chrome/Chromium if empty
    cookie_ttl_seconds: int = 1800  # 30 minutes
    # curl_cffi impersonation profile used for non-browser requests.
    # Must match a profile in curl_cffi.requests.BrowserType (chrome120, chrome131, etc).
    impersonate_profile: str = "chrome131"
    # Pre-warm the shared chromium instance at app startup so the first
    # user-triggered search doesn't pay the 5-8s cold-start tax. The warmup
    # runs as a background task — boot does not block on it. Disable on
    # memory-constrained hosts that don't always need the bypass (chromium
    # idle costs ~150 MB).
    warmup_on_startup: bool = True

    # ------------------------------------------------------------------
    # Per-phase timeouts (seconds). A protected request runs them in order:
    #   start browser → load page → wait for challenge JS → solve & recheck
    # The whole-operation budget is their SUM (see ``total_timeout_seconds``).
    # Bump these on slow NAS hardware where chromium and heavy Cloudflare
    # pages take longer.
    # ------------------------------------------------------------------
    # How long to wait for chromium to cold-start (DevTools endpoint up).
    # Used by the startup warmup, the first lazy launch, AND the startup-task
    # kick wait — one knob. Normally the browser is already warm and this phase
    # is skipped. On a weak NAS the FIRST cold start after a container recreate
    # competes with the boot storm (DB migration check, metadata refresh, the
    # frontend) and chromium can take ~2min just to expose DevTools — observed
    # 117s, tripping the old 120s budget by a hair. 240s gives that worst case
    # headroom; the cost is only paid on a genuinely slow cold start.
    browser_launch_timeout_seconds: float = 240.0
    # How long a single page navigation (``browser.get``) may take — opening +
    # attaching the tab and loading the challenge page. On a weak NAS the
    # tab-create + CDP attach can stall when chromium is CPU-starved by the boot
    # storm or a concurrent solve, so a tight cap times the navigation out
    # before the challenge can even be solved. 150s rides those stalls out.
    page_load_timeout_seconds: int = 150
    # How long to wait for Cloudflare's challenge JS (Turnstile) to render
    # before the first solve attempt. Lower it when sites clear quickly;
    # raise it if challenges aren't ready in time.
    challenge_wait_seconds: float = 10.0
    # How long to keep attempting + rechecking the challenge after it has
    # rendered, before giving up. Just the solve phase — NOT the whole op.
    # After the interstitial clears, the leftover of THIS budget is also used
    # to poll for the cf_clearance cookie (committed a beat after clearance,
    # via the Turnstile POST → Set-Cookie → redirect) before falling back to
    # the browser-fetch path. No separate knob — solve + cookie wait share it.
    solve_timeout_seconds: int = 180

    # Per-backend settings. Only the one named by ``solver`` is consulted.
    remote: RemoteSolverConfig = RemoteSolverConfig()
    byparr: ByparrConfig = ByparrConfig()
    flaresolverr: FlareSolverrConfig = FlareSolverrConfig()
    browser_run: BrowserRunConfig = BrowserRunConfig()
    firecrawl: FirecrawlConfig = FirecrawlConfig()

    @property
    def solver_name(self) -> str:
        """Normalised solver name (lowercased, empty → ``native``)."""
        return (self.solver or "native").strip().lower()

    @property
    def uses_external_solver(self) -> bool:
        """True when the solver hands fetches to an external HTTP service
        (no local/remote chromium under our control → skip browser warmup)."""
        return self.solver_name in EXTERNAL_SOLVERS

    @property
    def total_timeout_seconds(self) -> float:
        """Whole-operation budget = sum of the four per-phase timeouts.

        This is the outer cap applied to a full bypass (fetch or solve):
        browser launch + page load + challenge wait + solve.
        """
        return (
            self.browser_launch_timeout_seconds
            + self.page_load_timeout_seconds
            + self.challenge_wait_seconds
            + self.solve_timeout_seconds
        )

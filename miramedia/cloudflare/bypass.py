"""
Integrated Cloudflare challenge bypass using nodriver.

Uses a real (non-headless) Chrome browser with Xvfb to bypass Cloudflare
Turnstile challenges. The approach mirrors FlareSolverr: use keyboard
navigation (TAB + SPACE) to interact with the Turnstile checkbox inside
the cross-origin iframe, since the closed shadow DOM is inaccessible.

Cookies are cached per domain so subsequent requests avoid re-bypassing.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import requests as req

    from miramedia.cloudflare.config import CloudflareConfig
    from miramedia.cloudflare.solvers import CloudflareSolver

log = logging.getLogger(__name__)

# Optional human-readable progress sink for a solve. Invoked from the bypass
# worker loop, so implementations MUST be cheap + non-blocking (e.g. hand the
# message to an asyncio.Queue via ``loop.call_soon_threadsafe``). See the
# indexer-site test SSE endpoint for the consumer.
ProgressCb = Callable[[str], None]


class CFPageState(IntEnum):
    """Where a page sits in the Cloudflare challenge lifecycle.

    The solve loop drives off this instead of a blind timer: each state has a
    well-defined expectation for what happens next, so we react to the page
    rather than poke it on a metronome.

      UNKNOWN      — CDP eval failed/raced; treat as still-challenged, keep polling.
      CONTENT      — off the challenge, real page is up → success.
      INTERSTITIAL — "Just a moment…" orchestrator running, no widget yet →
                     expect it to either auto-pass (→CONTENT) or render a
                     checkbox (→WIDGET). Wait; do NOT reload (a reload restarts
                     the orchestrator's fingerprint/PoW run).
      WIDGET       — interactive Turnstile checkbox rendered + clickable → solve it.
      ERROR        — challenge JS errored (visible CF error element) → reload once.
      BLOCKED      — hard firewall block (1020 / "you have been blocked" /
                     "Access denied"); not a solvable challenge → fail fast so
                     the breaker trips instead of burning the whole budget.
    """

    UNKNOWN = 0
    CONTENT = 1
    INTERSTITIAL = 2
    WIDGET = 3
    ERROR = 4
    BLOCKED = 5


class CFChallengeType(IntEnum):
    """``window._cf_chl_opt.cType`` bucket — sets the expectation for an
    INTERSTITIAL: an INTERACTIVE challenge intends to render a checkbox (so a
    persistent no-widget interstitial means CF is withholding it = soft block),
    whereas a MANAGED/non-interactive one clears itself and never needs input.
    """

    UNKNOWN = 0
    INTERACTIVE = 1
    MANAGED = 2


def _emit(progress: ProgressCb | None, message: str) -> None:
    """Fire a progress callback, swallowing any error so progress reporting
    can never break the solve itself."""
    if progress is None:
        return
    try:
        progress(message)
    except Exception:
        log.debug("Progress callback failed", exc_info=True)


# Bypass operation timeouts live on ``CloudflareConfig``
# (browser_launch_timeout_seconds, page_load_timeout_seconds,
# solve_timeout_seconds) so they're settable from the settings UI. Read them
# via ``self.config.*`` at call time.

# Process-wide chromium concurrency cap (env-overridable).
#
# Distinct from the per-bypass-instance ``_get_fetch_semaphore`` (which is
# sized off ``indexers.native.max_concurrent_searches`` and bounds the number
# of concurrent tabs in the shared browser). This outer cap bounds chromium
# *operations* — every solve() acquires it. On a 2GB
# NAS the renderer + GPU + zygote processes for a single tab already cost
# ~150MB; allowing two concurrent challenges risks OOM. Default = 1.
_CHROMIUM_CONCURRENCY = max(1, int(os.getenv("MIRAMEDIA_CHROMIUM_CONCURRENCY", "1")))
_CHROMIUM_SEM: asyncio.Semaphore | None = None


def _get_chromium_sem() -> asyncio.Semaphore:
    """Lazy-init the process-wide chromium semaphore on the running loop.

    Module-level construction would bind it to whichever loop happened to be
    current at import (often none / the wrong one), producing "different
    loop" errors when the bypass worker loop tries to acquire it.
    """
    global _CHROMIUM_SEM
    if _CHROMIUM_SEM is None:
        _CHROMIUM_SEM = asyncio.Semaphore(_CHROMIUM_CONCURRENCY)
    return _CHROMIUM_SEM


class MissingChromiumError(RuntimeError):
    """No Chromium binary is available for the native solver.

    Raised before launch so the caller can log one actionable line (pull the
    ``-cf`` image variant or set ``[cloudflare] browser_path``) instead of a raw
    nodriver ``FileNotFoundError`` traceback on every solve attempt.
    """


@dataclass
class CachedSession:
    cookies: dict[str, str]
    user_agent: str
    expires_at: float


def is_cloudflare_challenge(response: req.Response) -> bool:
    """Detect whether a response is a Cloudflare challenge page."""
    if response.status_code not in (403, 503):
        return False

    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True

    server = response.headers.get("server", "").lower()
    if "cloudflare" in server:
        body = response.text[:2000] if response.text else ""
        markers = (
            "Just a moment...",
            "cf-browser-verification",
            "challenge-platform",
            "cf_clearance",
            "_cf_chl",
        )
        if any(m in body for m in markers):
            return True

    return False


class CloudflareBypass:
    def __init__(self, config: CloudflareConfig) -> None:
        self.config = config
        self._cache: dict[str, CachedSession] = {}
        self._lock = threading.Lock()
        self._domain_locks: dict[str, threading.Lock] = {}
        # Dedicated asyncio worker (lazy) so all solves share one event loop;
        # see _ensure_worker_loop for why.
        self._worker_lock = threading.Lock()
        self._worker_loop: asyncio.AbstractEventLoop | None = None
        self._worker_thread: threading.Thread | None = None
        # Long-lived chromium instance reused across solves + fetches. Cold
        # starting chromium each fetch costs 5-8s; with this we pay that
        # once per process and every subsequent navigation is ~1s.
        self._shared_browser = None
        self._shared_browser_pid: int | None = None
        # Asyncio gate around ``_ensure_shared_browser`` so concurrent fetches
        # can't race the launch path (only one coroutine starts chromium even
        # if multiple find ``_shared_browser is None`` at once).
        self._launch_lock: asyncio.Lock | None = None
        # PIDs and profile dirs we have spawned. Cleanup methods kill/remove
        # *only* what's in these sets — never a blanket ``pgrep -f chrom`` /
        # ``/tmp/.org.chromium.*`` sweep, which historically killed sibling
        # chromiums from concurrent paths and wiped active profile dirs.
        self._tracked_pids: set[int] = set()
        self._tracked_profiles: set[str] = set()
        self._tracked_lock = threading.Lock()
        # Cross-thread "shared browser is live" gate. threading.Event so it
        # can be awaited from any event loop (lifespan startup gate, etc).
        # Set when ``_ensure_shared_browser_locked`` brings chromium up;
        # cleared on teardown / recycle.
        self._ready_event = threading.Event()
        # Cap concurrent chromium tabs to keep renderer-process count
        # bounded. Sized off the indexer's ``max_concurrent_searches`` —
        # rebuilt lazily so config reloads take effect on the next fetch.
        self._fetch_semaphore: asyncio.Semaphore | None = None
        self._fetch_semaphore_limit: int = 0
        # Recycle the shared chromium periodically. nodriver's ``tab.close()``
        # doesn't always reap the renderer process; over time the chromium
        # subprocess tree grows, exhausts file descriptors, and kills the
        # whole container (DNS resolves stop, asyncpg connections drop, app
        # spins on "connection is lost"). Restarting cleanly every N fetches
        # keeps the FD budget under control.
        self._fetch_count: int = 0
        self._fetch_count_lock = threading.Lock()
        self._max_fetches_per_browser: int = int(
            os.getenv("MIRAMEDIA_BYPASS_MAX_FETCHES", "25")
        )
        # Per-domain circuit breaker. A domain that fails to bypass keeps
        # eating the full ``solve`` timeout (up to 120s) on every retry,
        # and the auto-download sweeps hammer the same dead host repeatedly —
        # minutes of wall-clock burned per sweep, and the long idle gaps that
        # trip the taskiq broker's idle-in-transaction reaper. After
        # ``_breaker_threshold`` consecutive failures we OPEN the breaker for
        # ``_breaker_cooldown`` seconds: ``solve`` returns None instantly
        # for that domain instead of launching chromium. A single success
        # (half-open probe after cooldown) resets it.
        self._domain_fail_count: dict[str, int] = {}
        self._domain_open_until: dict[str, float] = {}
        self._breaker_lock = threading.Lock()
        self._breaker_threshold: int = max(
            1, int(os.getenv("MIRAMEDIA_BYPASS_BREAKER_THRESHOLD", "3"))
        )
        self._breaker_cooldown: float = float(
            os.getenv("MIRAMEDIA_BYPASS_BREAKER_COOLDOWN", "600")
        )
        # Lazily-built external solver (byparr/flaresolverr/browser_run/
        # firecrawl). None for native/remote, which drive the nodriver loop.
        self._external_solver = None
        # One-shot: log the "no chromium in this image" hint once, not per solve.
        self._missing_chromium_warned = False

    @staticmethod
    def _domain_of(url: str) -> str:
        from urllib.parse import urlparse

        return (urlparse(url).netloc or url).lower()

    def _breaker_is_open(self, domain: str) -> bool:
        """True if ``domain`` is in an open-breaker cooldown window."""
        with self._breaker_lock:
            until = self._domain_open_until.get(domain)
            if until is None:
                return False
            if time.monotonic() >= until:
                # Cooldown elapsed → half-open: allow one probe through.
                del self._domain_open_until[domain]
                return False
            return True

    def _breaker_record_success(self, domain: str) -> None:
        with self._breaker_lock:
            self._domain_fail_count.pop(domain, None)
            self._domain_open_until.pop(domain, None)

    def _breaker_record_failure(self, domain: str) -> None:
        with self._breaker_lock:
            count = self._domain_fail_count.get(domain, 0) + 1
            self._domain_fail_count[domain] = count
            if count >= self._breaker_threshold:
                self._domain_open_until[domain] = (
                    time.monotonic() + self._breaker_cooldown
                )
                log.warning(
                    "Cloudflare bypass circuit breaker OPEN for %s after %d "
                    "consecutive failures; skipping for %.0fs",
                    domain,
                    count,
                    self._breaker_cooldown,
                )

    def invalidate_cache(self, domain: str) -> None:
        """Drop any cached session for ``domain`` so the next solve() call
        actually launches chromium instead of returning a stale empty
        session that triggered the caller's CF challenge in the first
        place."""
        with self._lock:
            self._cache.pop(domain, None)

    def get_cached_session(self, domain: str) -> CachedSession | None:
        with self._lock:
            cached = self._cache.get(domain)
            if cached and cached.expires_at > time.monotonic() and cached.cookies:
                return cached
            if cached:
                del self._cache[domain]
            return None

    # CF cookies that let curl_cffi skip the Turnstile challenge next time.
    # cf_clearance is the durable challenge-pass token (browser-minted);
    # __cf_bm is CF's bot-management cookie, rotated on most responses.
    _CF_COOKIE_NAMES = ("cf_clearance", "__cf_bm")

    def refresh_cache_from_cookies(
        self,
        domain: str,
        cookies: dict[str, str],
        user_agent: str | None = None,
    ) -> None:
        """Fold CF cookies seen on a SUCCESSFUL curl_cffi response back into the
        per-domain cache and bump its TTL.

        When curl_cffi rides through without hitting a challenge, CF still hands
        back (and rotates) ``__cf_bm`` and can refresh ``cf_clearance``. Folding
        those back keeps the shared cache warm so later requests — and a cold
        ``CloudflareSession`` after a restart — keep skipping the browser solve
        instead of re-triggering the Turnstile. Merges onto any existing entry
        (so a browser-minted cf_clearance survives a response that only rotated
        __cf_bm) and only acts when a CF cookie is present, so plain site
        cookies never create a junk entry.
        """
        cf = {k: v for k, v in cookies.items() if k in self._CF_COOKIE_NAMES and v}
        if not cf:
            return
        domain = domain.lower()
        with self._lock:
            existing = self._cache.get(domain)
            merged = dict(existing.cookies) if existing else {}
            merged.update(cf)
            self._cache[domain] = CachedSession(
                cookies=merged,
                user_agent=user_agent or (existing.user_agent if existing else ""),
                expires_at=time.monotonic() + self.config.cookie_ttl_seconds,
            )

    def _get_domain_lock(self, domain: str) -> threading.Lock:
        with self._lock:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = threading.Lock()
            return self._domain_locks[domain]

    def await_pending_solve(self, domain: str, timeout: float | None = None) -> None:
        if timeout is None:
            timeout = self.config.total_timeout_seconds + 10
        domain_lock = self._get_domain_lock(domain)
        if domain_lock.acquire(blocking=False):
            domain_lock.release()
            return
        log.debug(f"Waiting for in-progress Cloudflare solve for {domain}")
        if domain_lock.acquire(timeout=timeout):
            domain_lock.release()

    @staticmethod
    def _ensure_display() -> None:
        if os.environ.get("DISPLAY"):
            return
        try:
            out = subprocess.check_output(
                ["pgrep", "-a", "Xvfb"],  # noqa: S607 — pgrep resolved via PATH, intentional
                text=True,
                timeout=2,
            )
            for part in out.split():
                if part.startswith(":"):
                    os.environ["DISPLAY"] = part
                    log.info(f"Set DISPLAY={part} from running Xvfb process")
                    return
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        if shutil.which("Xvfb"):
            os.environ["DISPLAY"] = ":99"
            log.info("Set DISPLAY=:99 (Xvfb available, assuming default)")

    # ------------------------------------------------------------------
    # Small reusable helpers — kept up top to avoid drift between
    # ``_async_solve_locked`` and the challenge flow it drives.
    # ------------------------------------------------------------------

    @staticmethod
    def _check_nodriver() -> bool:
        """Return True if nodriver is importable. Logs once on failure."""
        try:
            import nodriver  # noqa: F401
        except ImportError:
            log.exception("nodriver is not installed")
            return False
        else:
            return True

    @staticmethod
    async def _close_tab(page) -> None:  # noqa: ANN001
        """Close a nodriver Tab without raising. Browser stays alive.

        Bounded: ``page.close()`` is a CDP round-trip that hangs indefinitely
        against a wedged chromium. This runs in the ``finally`` of
        ``_async_solve_locked``; an unbounded hang here would stall the outer
        ``asyncio.wait_for`` (it can't force-cancel an unresponsive CDP await),
        so ``_async_solve`` never returns and never releases ``_CHROMIUM_SEM``
        (cap 1) → all future solves block forever. The 8s cap guarantees the
        finally completes and the semaphore is released even on a dead tab.
        """
        if page is None:
            return
        try:
            close = getattr(page, "close", None)
            if close is None:
                return
            result = close()
            if asyncio.iscoroutine(result):
                await asyncio.wait_for(result, timeout=8)
        except Exception:
            log.debug("Tab close failed", exc_info=True)

    @staticmethod
    async def _is_on_challenge_page(page) -> bool:  # noqa: ANN001
        """True iff page title looks like a Cloudflare interstitial.

        Used by both fetch and solve paths to decide whether to retry the
        keyboard solve or move on to the success path.
        """
        try:
            title = await asyncio.wait_for(page.evaluate("document.title"), timeout=8)
        except Exception:
            # Slow / busy chromium (NAS cold start, NetworkService restart) can
            # blow the title read past its timeout. Returning False here means
            # "cleared", which races a half-loaded page → solve declared done
            # before the challenge ran → 0 cookies harvested. Treat an
            # unreadable title as STILL challenged so the solve loop keeps
            # polling within its budget instead of bailing early.
            return True
        if not isinstance(title, str):
            return True
        return "Just a moment" in title or "Attention Required" in title

    @staticmethod
    async def _classify_page_state(
        page,  # noqa: ANN001
    ) -> tuple[CFPageState, CFChallengeType]:
        """Classify the page's Cloudflare lifecycle state in ONE CDP eval.

        Replaces the old binary ``(on_challenge, widget_present)`` probe with a
        proper state read so the solve loop can react to what the page is
        actually doing (see :class:`CFPageState`). Single round-trip — each eval
        costs up to ~6-8s on a starved NAS, so the per-second loop must not pay
        more than one.

        Returns ``(state, ctype)``. On eval failure returns
        ``(UNKNOWN, UNKNOWN)``: an unreadable page is treated as still-challenged
        (keep polling within budget) but we fire no keypresses at nothing.
        """
        try:
            res = await asyncio.wait_for(
                page.evaluate(
                    """
                    (() => {
                        const title = document.title || "";
                        const body = (document.body ? document.body.innerText : "") || "";
                        const opt = window._cf_chl_opt || null;
                        let ctype = 0;
                        if (opt && typeof opt.cType === 'string') {
                            ctype = opt.cType.toLowerCase() === 'interactive' ? 1 : 2;
                        }
                        // Hard firewall block (1020 / "you have been blocked" /
                        // managed "Access denied"). "Attention Required! |
                        // Cloudflare" with NO challenge orchestrator is the 1020
                        // block page, not a solvable challenge.
                        const blocked =
                            /you have been blocked/i.test(body) ||
                            /Error\\s*10\\d\\d/i.test(body) ||
                            title.includes("Access denied") ||
                            (title.includes("Attention Required") && !opt);
                        if (blocked) return [5, ctype];
                        // Interactive Turnstile checkbox rendered + clickable?
                        const ifr = document.querySelector(
                            'iframe[src*="challenges.cloudflare.com"]'
                        );
                        if (ifr) {
                            const r = ifr.getBoundingClientRect();
                            if (r.width >= 10 && r.height >= 10) return [3, ctype];
                        }
                        // Still on the interstitial (title or live orchestrator)?
                        const onChallenge = title.includes("Just a moment") || !!opt;
                        if (onChallenge) {
                            const err = document.querySelector(
                                '#challenge-error-text, #challenge-error-title'
                            );
                            // offsetParent !== null → actually visible (the
                            // <noscript> "Enable JavaScript" copy is hidden when
                            // JS runs, so it won't false-trip this).
                            if (err && err.offsetParent !== null) return [4, ctype];
                            return [2, ctype];
                        }
                        return [1, ctype];
                    })()
                    """,
                    return_by_value=True,
                ),
                timeout=8,
            )
        except Exception:
            return CFPageState.UNKNOWN, CFChallengeType.UNKNOWN
        vals = list(res) if isinstance(res, (list, tuple)) else []
        try:
            state = CFPageState(int(vals[0])) if len(vals) > 0 else CFPageState.UNKNOWN
            ctype = (
                CFChallengeType(int(vals[1]))
                if len(vals) > 1
                else CFChallengeType.UNKNOWN
            )
        except (ValueError, TypeError):
            return CFPageState.UNKNOWN, CFChallengeType.UNKNOWN
        return state, ctype

    @staticmethod
    async def _debug_dump_challenge(page, url: str) -> None:  # noqa: ANN001
        """TEMP: log a deep snapshot of the challenge page to tell apart an
        automation leak (``navigator.webdriver`` true, headless UA, missing
        plugins) from a withheld/wedged widget (interactive challenge that never
        renders its ``challenges.cloudflare.com`` iframe). One eval, best-effort.
        Logs at DEBUG and only runs the eval when DEBUG is enabled, so it stays
        permanently for diagnosis yet costs nothing at the default INFO level.
        (Dev forces root DEBUG, so it's live there — see dev-mode-forces-debug.)
        """
        if not log.isEnabledFor(logging.DEBUG):
            return
        js = r"""
        (() => {
            const rect = (el) => {
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return [Math.round(r.width), Math.round(r.height)];
            };
            const opt = window._cf_chl_opt || null;
            const body = (document.body ? document.body.innerText : "") || "";
            let webgl = "n/a";
            try {
                const gl = document.createElement("canvas").getContext("webgl");
                const dbg = gl && gl.getExtension("WEBGL_debug_renderer_info");
                if (dbg) webgl = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
            } catch (e) { webgl = "err"; }
            return JSON.stringify({
                title: document.title,
                url: location.href,
                webdriver: navigator.webdriver,
                ua: navigator.userAgent,
                langs: navigator.languages,
                plugins: navigator.plugins ? navigator.plugins.length : -1,
                hardwareConcurrency: navigator.hardwareConcurrency,
                webglRenderer: webgl,
                cfOpt: opt ? { cType: opt.cType, cRay: opt.cRay } : null,
                cfIframe: rect(document.querySelector(
                    'iframe[src*="challenges.cloudflare.com"]')),
                allIframes: [...document.querySelectorAll("iframe")].map((f) => ({
                    src: (f.src || "").slice(0, 90), size: rect(f),
                })),
                bodyLen: body.length,
                bodySnippet: body.slice(0, 280),
            });
        })()
        """
        try:
            res = await asyncio.wait_for(
                page.evaluate(js, return_by_value=True), timeout=10
            )
            log.debug("CF DEBUG DUMP %s -> %s", url, res)
        except Exception as exc:
            log.debug("CF DEBUG DUMP failed for %s: %s", url, exc)

    @staticmethod
    def _cookie_domain_matches(cookie_domain: str, domain_suffix: str) -> bool:
        """True if a CDP cookie's ``domain`` belongs to ``domain_suffix``
        (already lowercased, no leading dot). Host-only cookies (empty domain)
        belong to the page we navigated to, so they match.
        """
        cd = (cookie_domain or "").lstrip(".").lower()
        if not cd:
            return True
        return (
            cd == domain_suffix
            or domain_suffix.endswith("." + cd)
            or cd.endswith("." + domain_suffix)
        )

    async def _browser_has_cookie(self, page, domain: str, name: str) -> bool:  # noqa: ANN001
        """True if the browser currently holds cookie ``name`` scoped to
        ``domain``. Used as the authoritative "challenge actually passed"
        signal — the interstitial title can flip away before Turnstile issues
        cf_clearance, so title-gone alone is not enough.
        """
        domain_suffix = domain.lstrip(".").lower()
        try:
            from nodriver import cdp as _cdp

            cookies = await asyncio.wait_for(
                page.send(_cdp.storage.get_cookies()), timeout=8
            )
        except (TimeoutError, Exception):
            return False
        for c in cookies:
            if getattr(c, "name", None) != name:
                continue
            if getattr(c, "value", None) is None:
                continue
            if self._cookie_domain_matches(
                getattr(c, "domain", "") or "", domain_suffix
            ):
                return True
        return False

    async def _solve_challenge_on_page(
        self,
        page,  # noqa: ANN001
        url: str,
        settle_seconds: float | None = None,
        solve_timeout: float | None = None,
        domain: str | None = None,
        progress: ProgressCb | None = None,
    ) -> bool:
        """Settle the page, then drive a state machine off the live page state
        until the challenge clears or the solve budget runs out.

        Two phases:
          1. ``settle_seconds`` (``config.challenge_wait_seconds``) — let
             Cloudflare's challenge JS start running.
          2. ``solve_timeout`` (``config.solve_timeout_seconds``) — poll
             :meth:`_classify_page_state` once a second and react to it (see
             :class:`CFPageState`): solve a rendered checkbox, wait out a
             running interstitial, reload an errored/wedged one, and fail fast
             on a hard block — instead of reloading on a blind timer.

        Returns ``True`` if cleared within the budget, ``False`` otherwise.
        Cookie / HTML extraction is the caller's job — this only handles the
        challenge clearance gate so the solve flow has one place for it.

        ``domain`` makes success cf_clearance-aware: Cloudflare can flip the
        interstitial title away before Turnstile finishes issuing cf_clearance,
        and on slow hardware a single solve click often doesn't take. When a
        ``domain`` is given (the solve path, which exists to MINT cf_clearance
        for curl_cffi), we keep re-dispatching the solve until the cookie
        actually lands — not merely until the title changes. Without a
        ``domain`` (the browser-fetch path, which just needs the HTML and works
        for JS-only-gated sites that issue no cf_clearance) the title gate alone
        decides.
        """
        if settle_seconds is None:
            settle_seconds = self.config.challenge_wait_seconds
        if solve_timeout is None:
            solve_timeout = self.config.solve_timeout_seconds
        _emit(progress, "Waiting for Cloudflare to check your browser…")
        await asyncio.sleep(settle_seconds)

        # After the interstitial clears, wait at most this long for cf_clearance
        # to commit before accepting the page without it. The content is already
        # the result; the cookie is only a caching bonus for the next search.
        cookie_grace = 12.0
        # Re-dispatch the Turnstile solve on this WALL-CLOCK cadence (not poll
        # count): a single classify eval can cost ~5-6s on a CPU-starved NAS, so
        # a ``poll % N`` cadence stretches unpredictably. Real time keeps the
        # attempt rate hardware-independent (~12 tries per 180s budget).
        solve_interval = 15.0
        # An interstitial that makes no progress for this long is wedged or
        # (for an INTERACTIVE challenge) having its checkbox withheld by CF
        # (flagged IP / soft block). Generous on purpose: CF's own challenge
        # meta-refresh is 360s and the orchestrator needs uninterrupted time to
        # run its fingerprint/PoW — the old 25s metronome reset that mid-run and
        # could itself be why the widget never appeared. We reload at most
        # ``max_reloads`` times, then fail fast rather than loop the whole budget.
        stall_window = 60.0
        max_reloads = 2
        loop = asyncio.get_event_loop()
        deadline = loop.time() + solve_timeout
        last_solve_at: float | None = None
        off_since: float | None = None
        stall_since: float | None = None
        reloads = 0
        solve_attempts = 0

        async def _reload(reason: str) -> bool:
            """Reload once to re-trigger a wedged/withheld challenge. Returns
            False when the reload budget is spent (caller should give up)."""
            nonlocal reloads, last_solve_at, stall_since
            if reloads >= max_reloads:
                return False
            reloads += 1
            last_solve_at = None
            stall_since = None
            log.info(
                "CF challenge %s on %s; reloading page (%d/%d)",
                reason,
                url,
                reloads,
                max_reloads,
            )
            _emit(progress, "Challenge stalled; reloading page…")
            try:
                await asyncio.wait_for(page.reload(), timeout=15)
            except Exception:
                log.debug("Page reload failed", exc_info=True)
            return True

        while loop.time() < deadline:
            state, ctype = await self._classify_page_state(page)
            now = loop.time()

            if state is CFPageState.CONTENT:
                # Real page is up. (Don't accept on cf_clearance ALONE while
                # still on the interstitial: CF sets the cookie a beat before the
                # DOM redirects off "Just a moment". Here we're already off it.)
                if domain is None or await self._browser_has_cookie(
                    page, domain, "cf_clearance"
                ):
                    _emit(progress, "Challenge cleared")
                    return True
                # Content up but cf_clearance not committed yet; brief grace,
                # then accept without a cookie to cache.
                if off_since is None:
                    off_since = now
                elif now - off_since >= cookie_grace:
                    log.info(
                        "Challenge cleared for %s but cf_clearance not captured; "
                        "using the page without caching a cookie",
                        url,
                    )
                    _emit(progress, "Challenge cleared (no cookie to cache)")
                    return True

            elif state is CFPageState.WIDGET:
                off_since = stall_since = None
                # (Re)dispatch the solve on first sight of the checkbox, then
                # once per SOLVE_INTERVAL of wall-clock time.
                if last_solve_at is None or (now - last_solve_at) >= solve_interval:
                    last_solve_at = now
                    solve_attempts += 1
                    log.info(
                        "Solving Cloudflare Turnstile checkbox for %s (attempt %d)",
                        url,
                        solve_attempts,
                    )
                    _emit(
                        progress,
                        f"Solving Turnstile checkbox (attempt {solve_attempts})…",
                    )
                    try:
                        # Cap the interaction so a stalled CDP call can't block
                        # clear-detection: the challenge often auto-clears in the
                        # real browser WITHOUT our click, so the priority is to
                        # return to the poll loop and notice the clear.
                        await asyncio.wait_for(
                            self._solve_via_keyboard(page), timeout=12
                        )
                    except Exception:
                        log.debug("Turnstile solve attempt failed", exc_info=True)

            elif state is CFPageState.BLOCKED:
                # Hard firewall block (1020 / "you have been blocked"). Not
                # solvable by clicking — fail fast so the caller's breaker trips
                # instead of burning the whole budget re-polling it.
                log.warning(
                    "CF served a hard block for %s (not a solvable challenge); "
                    "giving up",
                    url,
                )
                _emit(progress, "Blocked by Cloudflare (not a solvable challenge)")
                return False

            elif state is CFPageState.ERROR:
                # Challenge JS errored. A reload re-triggers it; out of reloads,
                # it won't recover — give up.
                off_since = None
                if not await _reload("errored"):
                    log.warning(
                        "CF challenge errored on %s and reload budget spent; giving up",
                        url,
                    )
                    _emit(progress, "Challenge failed to run; giving up")
                    return False

            else:
                # INTERSTITIAL or UNKNOWN: "Just a moment…" orchestrator running
                # (or page unreadable), no checkbox yet. Expect it to auto-pass
                # or render a widget — WAIT, don't poke it. Only act on a stall.
                off_since = None
                if stall_since is None:
                    stall_since = now
                    _emit(progress, "Cloudflare is checking your browser…")
                    # TEMP DIAGNOSTIC: snapshot what the page actually is on first
                    # interstitial sight (automation-leak signals + widget state).
                    await self._debug_dump_challenge(page, url)
                elif now - stall_since >= stall_window:
                    # No transition for a full stall window. A MANAGED /
                    # non-interactive challenge clears itself given time, so keep
                    # waiting it out. An INTERACTIVE one should have shown a
                    # checkbox by now — CF is withholding it (flagged) or the
                    # orchestrator wedged: reload once, then give up rather than
                    # loop the whole budget.
                    if ctype is CFChallengeType.MANAGED:
                        stall_since = now  # reset the window; keep waiting
                    elif not await _reload("stalled with no checkbox"):
                        log.warning(
                            "CF interstitial on %s never produced a checkbox or "
                            "cleared (likely IP-flagged soft block); giving up",
                            url,
                        )
                        _emit(progress, "Challenge never appeared; giving up")
                        return False

            await asyncio.sleep(1)
        # Budget exhausted. Success = the interstitial actually cleared (the page
        # HTML is fetchable). The cf_clearance chase was best-effort — don't fail
        # a genuinely-cleared JS-only-gated page just because it issued no cookie.
        return not await self._is_on_challenge_page(page)

    async def _extract_and_cache_cookies(
        self,
        page,  # noqa: ANN001 — nodriver Tab, no static type available
        domain: str,
        deadline: float | None = None,
    ) -> CachedSession:
        """Harvest cookies + UA from a solved page and cache them per-domain.

        Called by the ``solve()`` path so the browser seeds the cf_clearance
        cache. That's what lets later fetches skip chromium and ride the cookie via
        curl_cffi until it expires (``cookie_ttl_seconds``). Caches only when
        a cookie was actually issued; returns the ``CachedSession`` either
        way so callers can report bypass success.
        """
        cookie_dict: dict[str, str] = {}
        # Storage.getCookies returns ALL cookies (no ``urls`` filter) — the
        # filter would drop cf_clearance when CF scoped it to the parent
        # domain or a different path than the solved URL.
        from nodriver import cdp as _cdp

        domain_suffix = domain.lstrip(".").lower()
        total = 0
        # cf_clearance lands a beat after the challenge interstitial clears,
        # so poll rather than read once and race the Set-Cookie. Break the
        # moment cf_clearance appears; otherwise keep polling until ``deadline``
        # (the leftover of the solve budget — see callers) and proceed with
        # whatever's there. JS-only-gated sites never issue cf_clearance and
        # wait the budget out, then fall through. ``deadline=None`` reads once
        # (no cookie wanted / no budget left). document.cookie can't
        # substitute — cf_clearance is HttpOnly.
        eloop = asyncio.get_event_loop()
        while True:
            try:
                cookies = await asyncio.wait_for(
                    page.send(_cdp.storage.get_cookies()),
                    timeout=10,
                )
            except (TimeoutError, Exception):
                log.debug("CDP cookie extraction failed", exc_info=True)
                cookies = []
            total = len(cookies)
            cookie_dict = {}
            for cookie in cookies:
                cookie_domain = (
                    (getattr(cookie, "domain", "") or "").lstrip(".").lower()
                )
                name = getattr(cookie, "name", None)
                value = getattr(cookie, "value", None)
                if name is None or value is None:
                    continue
                if not cookie_domain:
                    cookie_dict[name] = value
                elif (
                    cookie_domain == domain_suffix
                    or domain_suffix.endswith("." + cookie_domain)
                    or cookie_domain.endswith("." + domain_suffix)
                ):
                    cookie_dict[name] = value
            if (
                "cf_clearance" in cookie_dict
                or deadline is None
                or eloop.time() >= deadline
            ):
                break
            await asyncio.sleep(0.5)
        log.info(
            f"Extracted {len(cookie_dict)} cookies for {domain} via CDP "
            f"(out of {total} total in browser)"
        )

        if not cookie_dict:
            try:
                cookie_str = await asyncio.wait_for(
                    page.evaluate("document.cookie"), timeout=8
                )
                # nodriver returns ExceptionDetails (not a str) when the JS
                # eval errors on a slow/busy page — guard before .split.
                if isinstance(cookie_str, str) and cookie_str:
                    for part in cookie_str.split(";"):
                        part = part.strip()
                        if "=" in part:
                            k, v = part.split("=", 1)
                            cookie_dict[k.strip()] = v.strip()
            except (TimeoutError, Exception):
                log.debug("CF JS cookie fallback failed", exc_info=True)

        try:
            ua = await asyncio.wait_for(page.evaluate("navigator.userAgent"), timeout=8)
            user_agent = ua if isinstance(ua, str) else ""
        except (TimeoutError, Exception):
            user_agent = ""

        session = CachedSession(
            cookies=cookie_dict,
            user_agent=user_agent,
            expires_at=time.monotonic() + self.config.cookie_ttl_seconds,
        )

        if not cookie_dict:
            # Challenge cleared but no reusable cookie (JS-only gating, or CF
            # never issued cf_clearance). Nothing to cache — this solve's HTML
            # is still returned to the caller; the next request just re-solves
            # via the browser instead of riding a cookie.
            log.info(
                "No reusable cookie for %s after solve; not caching "
                "(next request re-solves via browser)",
                domain,
            )
            return session

        with self._lock:
            self._cache[domain] = session
        log.info(
            f"Cached Cloudflare session for {domain} "
            f"({len(cookie_dict)} cookies, TTL {self.config.cookie_ttl_seconds}s)"
        )
        return session

    @staticmethod
    def _filter_live_pids(pids: list[int]) -> list[int]:
        """Drop zombie / vanished PIDs from a list. Saves SIGKILLing
        <defunct> entries (no-op) and avoids racing with reapers.
        """
        live: list[int] = []
        for pid in pids:
            try:
                with Path(f"/proc/{pid}/status").open() as fh:
                    state_line = next(
                        (line for line in fh if line.startswith("State:")), ""
                    )
                if "Z" in state_line:
                    continue
            except (OSError, StopIteration):
                continue
            live.append(pid)
        return live

    @staticmethod
    def _reap_zombies() -> int:
        """Reap any child processes that have exited but weren't waited on.

        Without this, SIGKILL'd Chrome helpers linger as <defunct> entries
        and ``pgrep -f chrom`` keeps counting them on every retry, producing
        ever-growing "stale Chrome" lists.
        """
        reaped = 0
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            reaped += 1
        return reaped

    @staticmethod
    def _kill_chrome_tree(pid: int) -> None:
        try:
            child_pids = (
                subprocess.check_output(  # noqa: S603
                    ["pgrep", "-P", str(pid)],  # noqa: S607 — pgrep resolved via PATH, intentional
                    text=True,
                    timeout=2,
                )
                .strip()
                .split()
            )
            for cpid in child_pids:
                CloudflareBypass._kill_chrome_tree(int(cpid))
        except (subprocess.SubprocessError, FileNotFoundError, ValueError):
            pass

        try:
            os.kill(pid, signal.SIGKILL)
            log.debug(f"Killed Chrome process {pid}")
        except (ProcessLookupError, PermissionError):
            pass

        CloudflareBypass._reap_zombies()

    @staticmethod
    def _kill_chromium_by_profile(profile_dir: str) -> None:
        """Find chromium processes launched with ``--user-data-dir=<profile_dir>``
        and kill their process trees. Used to reap chromium spawned by a
        cancelled/failed ``uc.start`` call where we never captured the PID
        through the normal path.
        """
        try:
            # NB: the pattern must NOT start with a dash. ``pgrep -f
            # --user-data-dir=...`` makes pgrep parse the pattern as an
            # (unrecognised) option — it errors, prints its help, and matches
            # nothing, so the orphaned chromium tree is NEVER reaped. Over a
            # long-running container the leaked chromium processes exhaust FDs
            # and the kernel starts dropping sockets (asyncpg ``BrokenPipe`` /
            # ``connection is closed`` on live queries). Strip the leading
            # dashes — ``-f`` matches the full cmdline, so the dashless
            # substring still pins the right processes.
            out = subprocess.check_output(  # noqa: S603
                ["pgrep", "-f", f"user-data-dir={profile_dir}"],  # noqa: S607
                text=True,
                timeout=3,
            ).strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            return
        if not out:
            return
        # Find roots only (parent not in our matched set) so we don't
        # double-kill children that the tree kill will handle.
        pids = [int(p) for p in out.splitlines() if p.strip().isdigit()]
        if not pids:
            return
        pids_set = set(pids)
        roots: list[int] = []
        for pid in pids:
            try:
                with Path(f"/proc/{pid}/status").open() as fh:
                    ppid = 0
                    for line in fh:
                        if line.startswith("PPid:"):
                            ppid = int(line.split()[1])
                            break
                if ppid not in pids_set:
                    roots.append(pid)
            except (OSError, ValueError):
                roots.append(pid)
        log.info(
            f"Killing {len(roots)} chromium tree(s) bound to profile {profile_dir}: {roots}"
        )
        for root in roots:
            try:
                CloudflareBypass._kill_chrome_tree(root)
            except Exception:
                log.debug(
                    "Profile-targeted kill failed for pid %s", root, exc_info=True
                )

    def _kill_tracked_chrome(self) -> None:
        """Kill only chromium process trees we've explicitly spawned.

        Replaces the old ``pgrep -f chrom`` blanket sweep, which would kill
        any process matching the pattern — including sibling chromiums from
        concurrent code paths (solve while fetch in flight, etc.) and
        whatever the just-spawned launch was bringing up.
        """
        with self._tracked_lock:
            pids = list(self._tracked_pids)
            self._tracked_pids.clear()
        if not pids:
            CloudflareBypass._reap_zombies()
            return
        log.info(f"Killing {len(pids)} tracked Chrome process tree(s): {pids}")
        for pid in pids:
            try:
                CloudflareBypass._kill_chrome_tree(pid)
            except Exception:
                log.debug("Tracked tree kill failed for pid %s", pid, exc_info=True)
        CloudflareBypass._reap_zombies()

    def _nuke_all_chrome_if_enabled(self) -> None:
        """Emergency-only blanket ``pgrep -f chrom`` sweep, gated behind
        ``MIRAMEDIA_CHROMIUM_NUKE=1``. Off by default — see _kill_tracked_chrome
        for why. Useful when state is wedged and the container can't restart.
        """
        if os.getenv("MIRAMEDIA_CHROMIUM_NUKE") != "1":
            return
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "chrom"],  # noqa: S607 — pgrep resolved via PATH, intentional
                text=True,
                timeout=3,
            ).strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            return
        if not out:
            return
        pids = [int(p) for p in out.splitlines() if p.strip().isdigit()]
        live_pids = CloudflareBypass._filter_live_pids(pids)
        if live_pids:
            log.warning(
                f"MIRAMEDIA_CHROMIUM_NUKE=1: blanket-killing {len(live_pids)} chrome PID(s): {live_pids}"
            )
            for pid in live_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        CloudflareBypass._reap_zombies()

    def _track_chromium(self, pid: int | None, profile_dir: str | None) -> None:
        with self._tracked_lock:
            if pid is not None:
                self._tracked_pids.add(pid)
            if profile_dir is not None:
                self._tracked_profiles.add(profile_dir)

    def _forget_chromium(self, pid: int | None, profile_dir: str | None) -> None:
        with self._tracked_lock:
            if pid is not None:
                self._tracked_pids.discard(pid)
            if profile_dir is not None:
                self._tracked_profiles.discard(profile_dir)

    def _cleanup_tracked_profiles(self) -> None:
        with self._tracked_lock:
            dirs = list(self._tracked_profiles)
            self._tracked_profiles.clear()
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)

    # Back-compat shim — keep existing call sites working via the class method too.
    @staticmethod
    def is_cloudflare_challenge(response: req.Response) -> bool:
        return is_cloudflare_challenge(response)

    def solve_in_background(self, url: str) -> None:
        # Key by the same canonical (lowercased) host as solve()/the cookie
        # cache — a raw netloc with uppercase would key a different domain lock
        # and cache entry, silently defeating per-domain serialization and the
        # cookie reuse the curl_cffi side rides via await_pending_solve.
        domain = self._domain_of(url)

        if self.get_cached_session(domain):
            return
        domain_lock = self._get_domain_lock(domain)
        if not domain_lock.acquire(blocking=False):
            log.debug(f"Cloudflare bypass already in progress for {domain}")
            return
        domain_lock.release()

        thread = threading.Thread(target=self.solve, args=(url,), daemon=True)
        thread.start()

    _nodriver_patched = False
    _http_api_patched = False
    _ws_timeout_patched = False
    # websockets defaults the CDP opening-handshake to 10s. On a resource-tight
    # NAS, chromium's NetworkService crashes + auto-restarts once on cold start
    # ("Network service crashed or was terminated, restarting service") and the
    # browser main thread is busy through that window, so a 10s handshake races
    # it and fails ("timed out during opening handshake") even though DevTools
    # is up and chromium recovers. Give the handshake enough room to outlast the
    # restart instead of killing + relaunching the whole browser.
    _WS_OPEN_TIMEOUT_SECONDS = 45.0

    @classmethod
    def _patch_nodriver_connect_retries(
        cls, _uc: object, launch_timeout_seconds: float | None = None
    ) -> None:
        """Wrap ``Browser.start`` to:

        - extend the version-probe budget beyond nodriver's hardcoded 2.5s
          (containers + slow NAS can take 30-60s+ to expose DevTools);
        - tail chromium stderr live, breaking out the moment we see the
          ``DevTools listening on ws://...`` confirmation line — that's the
          authoritative "DevTools is ready" signal, no more polling
          guesswork;
        - bypass any HTTP proxy when probing ``http://127.0.0.1:<port>/json/version``
          (urllib honors ``http_proxy`` / system proxies for all hosts unless
          ``NO_PROXY`` is set — defensive: nodriver's HTTPApi uses urllib).

        Idempotent + cached across instances."""
        if cls._nodriver_patched:
            return
        from nodriver.core._contradict import ContraDict
        from nodriver.core.browser import Browser as _NDBrowser

        original_start = _NDBrowser.start

        # Upstream nodriver waits only 2.5s for DevTools; Synology-class
        # hardware can take 60-90s to expose it on cold start under load.
        # ``launch_timeout_seconds`` comes from CloudflareConfig.browser_launch_
        # timeout_seconds. The patch is applied + cached once per process, so
        # the first launch's value is authoritative process-wide.
        probe_interval = 0.5
        budget_seconds = max(probe_interval, float(launch_timeout_seconds or 120.0))

        cls._patch_nodriver_http_api()
        cls._patch_nodriver_ws_open_timeout()

        async def patched_start(self_) -> object:  # noqa: ANN001 — nodriver Browser imported lazily, no static type available
            # Kick off the live stderr tail BEFORE original_start runs the
            # nodriver-internal probe loop. Tail sets ``devtools_ready`` on
            # the "DevTools listening" line, and ``fatal_event`` on known-
            # unrecoverable conditions (network service crash → DevTools
            # HTTP responder wedges).
            devtools_ready = asyncio.Event()
            fatal_event = asyncio.Event()
            stderr_lines: list[str] = []
            tail_task: asyncio.Task | None = None

            def _start_tail() -> None:
                nonlocal tail_task
                proc = getattr(self_, "_process", None)
                if proc is None or proc.stderr is None:
                    return
                if tail_task is not None:
                    return
                tail_task = asyncio.create_task(
                    cls._tail_chromium_stderr(
                        proc.stderr, devtools_ready, stderr_lines, fatal_event
                    )
                )

            try:
                try:
                    await original_start(self_)
                except Exception:
                    log.debug(
                        "chromium original_start failed; falling back to manual probe",
                        exc_info=True,
                    )
                else:
                    _start_tail()
                    return self_
                _start_tail()
                end_time = asyncio.get_event_loop().time() + budget_seconds
                while asyncio.get_event_loop().time() < end_time:
                    if fatal_event.is_set():
                        log.warning(
                            "chromium emitted a fatal-state line (network service "
                            "crashed / GPU launch failure); aborting probe early"
                        )
                        break
                    if devtools_ready.is_set():
                        try:
                            info = await self_._http.get("version")
                            self_.info = ContraDict(info, silent=True)
                            if self_.info:
                                break
                        except Exception as exc:
                            # Expected during cold start (DevTools port not open
                            # yet) — terse, no traceback. A NAS that takes 100+
                            # retries would otherwise dump 100+ stack traces.
                            # The genuine-failure path below logs chromium stderr
                            # if the probe never converges.
                            log.debug(
                                "DevTools version probe failed; retrying (%s)", exc
                            )
                    else:
                        # Tail hasn't seen the marker — try a regular probe
                        # in case stderr buffering ate the line.
                        try:
                            info = await self_._http.get("version")
                            self_.info = ContraDict(info, silent=True)
                            if self_.info:
                                break
                        except Exception as exc:
                            # Expected during cold start (DevTools port not open
                            # yet) — terse, no traceback. A NAS that takes 100+
                            # retries would otherwise dump 100+ stack traces.
                            # The genuine-failure path below logs chromium stderr
                            # if the probe never converges.
                            log.debug(
                                "DevTools version probe failed; retrying (%s)", exc
                            )
                    try:
                        await asyncio.wait_for(
                            devtools_ready.wait(), timeout=probe_interval
                        )
                    except TimeoutError:
                        pass
                if not getattr(self_, "info", None):
                    cls._log_stderr_lines(stderr_lines)
                    await cls._dump_chromium_stderr(self_)
                    reason = (
                        "network service crashed early"
                        if fatal_event.is_set()
                        else f"{budget_seconds:.0f}s wait exhausted"
                    )
                    msg = f"miramedia: chromium DevTools never came up ({reason})"
                    raise Exception(msg)  # noqa: TRY002, TRY301 — generic launch failure, re-raised verbatim to existing broad callers
                self_.websocket_url = self_.info.webSocketDebuggerUrl
                await self_.attach()
                return self_  # noqa: TRY300 — must stay in try; preceding attach() is guarded by the except below
            except BaseException:
                # Includes asyncio.CancelledError. Surface whatever chromium
                # printed before we bail — without this the only thing
                # downstream sees is "launch cancelled" with no context.
                if stderr_lines:
                    cls._log_stderr_lines(stderr_lines)
                raise
            finally:
                if tail_task is not None and not tail_task.done():
                    tail_task.cancel()

        _NDBrowser.start = patched_start
        cls._nodriver_patched = True
        log.debug(
            "Patched nodriver Browser.start with %.0fs probe window + live stderr tail",
            budget_seconds,
        )

    @staticmethod
    async def _tail_chromium_stderr(
        stream: asyncio.StreamReader,
        ready_event: asyncio.Event,
        lines_out: list[str],
        fatal_event: asyncio.Event | None = None,
    ) -> None:
        """Read chromium stderr line-by-line.

        - Set ``ready_event`` on ``DevTools listening on ws://`` (the
          authoritative ready marker — break probe loop right away).
        - Set ``fatal_event`` only on genuinely-unrecoverable conditions:
          a GPU-process launch failure, or a *repeated* NetworkService
          crash (a restart loop). A SINGLE ``Network service crashed ...
          restarting service`` line is recoverable — chromium 148 emits it
          on cold start under NAS memory pressure, restarts the service
          out-of-process, and DevTools (served by the browser process, not
          the network service) stays up. Treating that lone blip as fatal
          was killing browsers that would have come up fine.
        - Append all lines into ``lines_out`` so we can dump them later
          if the probe never converges.
        """
        # chromium auto-restarts a crashed NetworkService. One restart is
        # normal; a tight loop of them means it can't stay up — only then
        # is it worth aborting the probe early.
        ns_crash_fatal_threshold = 3
        ns_crashes = 0
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                if text:
                    lines_out.append(text)
                if "DevTools listening on ws://" in text:
                    ready_event.set()
                if fatal_event is not None:
                    if "Failed to launch GPU process" in text:
                        fatal_event.set()
                    elif "Network service crashed" in text:
                        ns_crashes += 1
                        if ns_crashes >= ns_crash_fatal_threshold:
                            fatal_event.set()
                if len(lines_out) > 200:
                    del lines_out[: len(lines_out) - 200]
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("chromium stderr tail failed", exc_info=True)

    # Substrings of chromium stderr lines that are benign in a headless,
    # dbus-less, GPU-less container. They are emitted at chromium's ERROR
    # level but are not actionable — chromium falls back fine. Filtered out of
    # our captured stderr so a real crash line isn't buried under dozens of
    # ``Failed to connect to the bus`` repeats.
    _BENIGN_STDERR_MARKERS = (
        "dbus/bus.cc",
        "Failed to connect to the bus",
        "Failed to connect to socket /run/dbus",
        "bluez",
        "floss_manager",
        "GpuChannelManager",
        "viz::GpuServiceImpl",
    )

    @classmethod
    def _filter_chromium_noise(cls, lines: list[str]) -> list[str]:
        """Drop known-benign chromium stderr lines (dbus / bluetooth / GPU)."""
        return [
            ln for ln in lines if not any(m in ln for m in cls._BENIGN_STDERR_MARKERS)
        ]

    @classmethod
    def _log_stderr_lines(cls, lines: list[str]) -> None:
        if not lines:
            return
        meaningful = cls._filter_chromium_noise(lines)
        if not meaningful:
            log.debug(
                "chromium stderr: %d line(s), all benign (dbus/gpu noise)",
                len(lines),
            )
            return
        log.error(
            "chromium stderr (last %d line(s) before timeout):\n%s",
            len(meaningful),
            "\n".join(meaningful[-50:]),
        )

    @classmethod
    def _patch_nodriver_http_api(cls) -> None:
        """Replace nodriver's HTTPApi opener with one that ignores system /
        env proxies. urllib.request.urlopen by default honors HTTP_PROXY,
        ``/etc/environment`` proxies, and (on macOS) the system network
        prefs — which routes 127.0.0.1 probes through a proxy that doesn't
        exist, surfacing as a generic timeout. Force a direct opener.

        Idempotent.
        """
        if getattr(cls, "_http_api_patched", False):
            return
        import urllib.request

        from nodriver.core.browser import HTTPApi

        async def patched_request(
            self,  # noqa: ANN001 — patched HTTPApi method, dynamic nodriver types
            endpoint,  # noqa: ANN001
            method: str = "get",
            data=None,  # noqa: ANN001
        ) -> object:
            import json as _json
            import urllib.parse as _up

            url = _up.urljoin(self.api, f"json/{endpoint}" if endpoint else "/json")
            if data and method.lower() == "get":
                msg = "get requests cannot contain data"
                raise ValueError(msg)
            # URL is always nodriver's local DevTools endpoint (127.0.0.1
            # http) built from ``self.api`` — not user input, no file:/custom
            # scheme reachable. S310 is a false positive here.
            req = urllib.request.Request(url)  # noqa: S310
            req.method = method
            req.data = _json.dumps(data).encode("utf-8") if data else None
            # Bypass any system / env proxy — DevTools is local-only.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: opener.open(req, timeout=10)
            )
            return _json.loads(response.read())

        HTTPApi._request = patched_request
        cls._http_api_patched = True

    @classmethod
    def _patch_nodriver_ws_open_timeout(cls) -> None:
        """Inject a larger ``open_timeout`` into nodriver's CDP websocket
        connect. nodriver calls ``websockets.connect(url, ping_timeout=...,
        max_size=...)`` with no ``open_timeout``, so the opening handshake uses
        the websockets default (10s). That's too tight on a NAS where chromium's
        NetworkService crashes + restarts once on cold start — see
        ``_WS_OPEN_TIMEOUT_SECONDS``. Wrapping ``connect`` to default the
        ``open_timeout`` lets the handshake outlast the restart window.

        Idempotent.
        """
        if cls._ws_timeout_patched:
            return
        from nodriver.core import connection as _conn

        original_connect = _conn.websockets.connect

        def patched_connect(*args, **kwargs) -> object:  # noqa: ANN002, ANN003 — passthrough to websockets.connect
            kwargs.setdefault("open_timeout", cls._WS_OPEN_TIMEOUT_SECONDS)
            return original_connect(*args, **kwargs)

        _conn.websockets.connect = patched_connect
        cls._ws_timeout_patched = True

    @staticmethod
    async def _dump_chromium_stderr(browser) -> None:  # noqa: ANN001
        """Drain whatever chromium has written to stderr/stdout (asyncio
        StreamReaders piped by nodriver) and log it. If chromium is hung but
        alive we won't get a clean EOF, so we kill the process first to flush
        the pipes, then read with a short timeout.
        """
        proc = getattr(browser, "_process", None)
        if proc is None:
            log.error("chromium failed to start (no process handle to inspect)")
            return

        rc = proc.returncode
        if rc is None:
            # Still alive but DevTools never came up — kill so the pipes close.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass
            rc = proc.returncode

        async def _drain(stream: asyncio.StreamReader | None, label: str) -> str:
            if stream is None:
                return ""
            try:
                data = await asyncio.wait_for(stream.read(), timeout=2.0)
            except TimeoutError:
                log.warning(
                    "chromium %s drain timed out — pipe likely still open", label
                )
                return ""
            except RuntimeError as exc:
                # nodriver's own transport already owns this StreamReader, so
                # our best-effort diagnostic read collides with it:
                # ``RuntimeError: read() called while another coroutine is
                # already waiting for incoming data``. Expected during teardown
                # — debug, not an ERROR+traceback.
                log.debug("chromium %s drain skipped (%s)", label, exc)
                return ""
            except Exception:
                log.exception("chromium %s drain failed", label)
                return ""
            return data.decode(errors="replace").strip()

        stderr_text = await _drain(getattr(proc, "stderr", None), "stderr")
        stdout_text = await _drain(getattr(proc, "stdout", None), "stdout")

        if stderr_text:
            meaningful = "\n".join(
                CloudflareBypass._filter_chromium_noise(stderr_text.splitlines())
            ).strip()
            if meaningful:
                log.error(
                    "chromium subprocess (pid=%s, rc=%s) stderr:\n%s",
                    proc.pid,
                    rc,
                    meaningful,
                )
        if stdout_text:
            log.error(
                "chromium subprocess (pid=%s, rc=%s) stdout:\n%s",
                proc.pid,
                rc,
                stdout_text,
            )
        if not stderr_text and not stdout_text:
            log.error(
                "chromium subprocess (pid=%s, rc=%s): no stderr/stdout captured",
                proc.pid,
                rc,
            )

    def solve(
        self,
        url: str,
        timeout: float | None = None,
        progress: ProgressCb | None = None,
    ) -> str | None:
        """Solve any Cloudflare challenge on ``url`` with the long-lived bypass
        browser and return the resulting page HTML (or None).

        ``progress`` is an optional sink for human-readable phase messages
        ("Navigating…", "Solving Turnstile…", "Challenge cleared"). It's called
        from the bypass worker loop, so it must be cheap + non-blocking.

        The browser owns the entire challenge flow: it keeps re-engaging the
        Turnstile until the interstitial clears AND polls for the cf_clearance
        cookie until it lands or the solve budget runs out. The cookie is cached
        so the NEXT request to the domain can ride it via curl_cffi and skip
        chromium entirely. Real chromium under nodriver already speaks the
        TLS/JA3 fingerprint Cloudflare expects, so the page HTML comes straight
        back from this navigation — no curl_cffi round-trip for the current
        request (cf_clearance is bound to the originating fingerprint, and sites
        like 1337x check more than the cookie).

        The browser is kept alive across calls; only the first solve per process
        pays the ~5-8s chromium cold-start. Per-domain serialized: a second
        caller for the same domain waits on the first (so does curl_cffi, via
        ``await_pending_solve``) and then rides the freshly-cached cookie instead
        of launching a duplicate solve.
        """
        if not self.config.enabled:
            log.debug("Cloudflare bypass disabled; skipping solve of %s", url)
            return None
        # ``timeout`` is the whole-operation budget. It defaults to the sum of
        # the per-phase timeouts (``total_timeout_seconds``): browser launch +
        # page load + challenge wait + solve. The navigate sub-cap is
        # ``page_load_timeout_seconds`` inside the locked body; the solve phase
        # is bounded by ``solve_timeout_seconds``.
        if timeout is None:
            timeout = self.config.total_timeout_seconds
        domain = self._domain_of(url)
        if self._breaker_is_open(domain):
            log.debug(
                "Cloudflare bypass breaker open for %s; skipping solve of %s",
                domain,
                url,
            )
            _emit(
                progress,
                "Cloudflare bypass temporarily disabled for this site "
                "(recent failures); skipping",
            )
            return None

        # Serialize browser solves per domain: concurrent searches don't each
        # launch chromium, and ``await_pending_solve`` (the curl_cffi side) has
        # a lock to wait on. The winner solves + caches the cookie; the rest
        # navigate with it already present (fast clear).
        domain_lock = self._get_domain_lock(domain)
        if not domain_lock.acquire(timeout=timeout + 5):
            log.warning("Timed out waiting for Cloudflare solve lock for %s", domain)
            _emit(progress, "Another solve is already running for this site")
            return None
        try:
            if self.config.uses_external_solver:
                # byparr/flaresolverr/browser_run/firecrawl: hand the fetch to
                # the service over HTTP. No local chromium, no worker loop.
                result = self._solve_via_external(url, progress)
            else:
                # native / remote: full nodriver solve loop on the worker loop.
                loop = self._ensure_worker_loop()
                future = asyncio.run_coroutine_threadsafe(
                    self._async_solve(url, timeout, progress), loop
                )
                try:
                    result = future.result(timeout=timeout + 10)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    log.warning("Cloudflare solve timed out for %s", url)
                    self._breaker_record_failure(domain)
                    return None
                except MissingChromiumError:
                    # Already warned once with an actionable hint; no traceback.
                    self._breaker_record_failure(domain)
                    return None
                except Exception:
                    log.exception("Cloudflare solve failed for %s", url)
                    self._breaker_record_failure(domain)
                    return None
        finally:
            domain_lock.release()

        # A solve that returns no HTML is a functional failure too — count it so
        # a consistently-blocked domain trips the breaker like a hard timeout.
        if result:
            self._breaker_record_success(domain)
        else:
            self._breaker_record_failure(domain)
        return result

    def _get_external_solver(self) -> CloudflareSolver:
        """Lazily build + cache the configured external solver."""
        if self._external_solver is None:
            from miramedia.cloudflare.solvers import get_solver

            self._external_solver = get_solver(self.config)
        return self._external_solver

    def _solve_via_external(self, url: str, progress: ProgressCb | None) -> str | None:
        """Run an external HTTP solver and fold its cookies into the shared
        cache so curl_cffi can ride them next time (when the backend exposes
        them). Returns the page HTML, or None on any failure."""
        try:
            solver = self._get_external_solver()
        except Exception:
            log.exception("Failed to build external Cloudflare solver")
            return None
        try:
            res = solver.solve(url, progress)
        except Exception:
            log.exception("External Cloudflare solver raised for %s", url)
            return None
        if res is None:
            return None
        if res.cookies:
            try:
                self.refresh_cache_from_cookies(
                    self._domain_of(url), res.cookies, res.user_agent or None
                )
            except Exception:
                log.debug("External solver cache write failed", exc_info=True)
        return res.html

    def _get_fetch_semaphore(self) -> asyncio.Semaphore:
        """Concurrency cap for chromium tabs.

        Tracks ``indexers.native.max_concurrent_searches`` (default 5). One
        tab = one renderer process — without this cap, parallel SSE search
        chunks could fan out a dozen tabs and balloon the chromium process
        tree. Rebuilt when the configured limit changes so settings updates
        take effect on the next fetch.
        """
        from miramedia.config import MiraMediaConfig

        try:
            limit = max(
                1,
                int(MiraMediaConfig().indexers.native.max_concurrent_searches),
            )
        except Exception:
            limit = 5
        if self._fetch_semaphore is None or self._fetch_semaphore_limit != limit:
            self._fetch_semaphore = asyncio.Semaphore(limit)
            self._fetch_semaphore_limit = limit
        return self._fetch_semaphore

    def _get_launch_lock(self) -> asyncio.Lock:
        if self._launch_lock is None:
            self._launch_lock = asyncio.Lock()
        return self._launch_lock

    async def _ensure_shared_browser(self) -> object:
        """Lazily start + return the shared chromium instance. Restarts if
        the previous instance died unexpectedly OR if the per-browser fetch
        budget was exhausted (recycle to prevent renderer-process buildup).

        Wrapped in a per-instance asyncio.Lock so concurrent fetches can't
        race the launch path — only the first coroutine through actually
        spawns chromium; the rest wait and reuse it.
        """
        async with self._get_launch_lock():
            return await self._ensure_shared_browser_locked()

    async def _ensure_shared_browser_locked(self) -> object:
        existing = self._shared_browser

        with self._fetch_count_lock:
            count = self._fetch_count
        if (
            existing is not None
            and self._max_fetches_per_browser > 0
            and count >= self._max_fetches_per_browser
        ):
            log.info(
                "Cloudflare bypass: recycling shared chromium after %d fetches",
                count,
            )
            await self._teardown_shared_browser()
            existing = None

        # A remote (connect-existing) browser has no local ``_process``, so the
        # process-liveness probe below would tear it down on every fetch. Skip
        # the probe for remote — we don't own that process; if the connection
        # dies a navigation will raise and recover lazily.
        if existing is not None and self._remote_endpoint() is None:
            try:
                proc = getattr(existing, "_process", None) or getattr(
                    existing, "process", None
                )
                if proc is None or (
                    hasattr(proc, "returncode") and proc.returncode is not None
                ):
                    existing = None
            except Exception:
                existing = None
            if existing is None:
                await self._teardown_shared_browser()
        if existing is None:
            with self._fetch_count_lock:
                self._fetch_count = 0
            browser = await self._start_browser()
            self._shared_browser = browser
            try:
                proc = getattr(browser, "_process", None) or getattr(
                    browser, "process", None
                )
                self._shared_browser_pid = proc.pid if proc else None
            except Exception:
                self._shared_browser_pid = None
            # Flag the bypass as live so anything that explicitly waited on
            # ``await_ready`` (e.g. lifespan task kick-off) can proceed.
            self._ready_event.set()
        return self._shared_browser

    async def _teardown_shared_browser(self) -> None:
        """Stop the shared chromium and reap *only* its process tree + its
        profile dir. No blanket ``pgrep`` sweep, no ``/tmp/*.chromium*`` rm —
        those used to clobber siblings.
        """
        shared = self._shared_browser
        shared_pid = self._shared_browser_pid
        self._shared_browser = None
        self._shared_browser_pid = None
        # Drop the "ready" flag so callers awaiting ``await_ready`` after a
        # recycle won't immediately see stale True.
        self._ready_event.clear()
        if shared is not None:
            try:
                shared.stop()
            except Exception:
                log.debug("Shared browser stop() failed", exc_info=True)
        if shared_pid:
            try:
                CloudflareBypass._kill_chrome_tree(shared_pid)
            except Exception:
                log.debug("Shared browser tree kill failed", exc_info=True)
            self._forget_chromium(pid=shared_pid, profile_dir=None)
        # Reap any leftover zombies + remove all tracked profile dirs (the
        # shared browser was the only one this instance owned).
        CloudflareBypass._reap_zombies()
        self._cleanup_tracked_profiles()

    async def _async_solve(
        self,
        url: str,
        timeout: float,  # noqa: ASYNC109 — forwarded to asyncio.wait_for
        progress: ProgressCb | None = None,
    ) -> str | None:
        if not CloudflareBypass._check_nodriver():
            return None
        # Outer cap: process-wide chromium-op budget (NAS-tunable via env).
        # Inner cap: per-instance tab budget tracking the configured
        # max_concurrent_searches. The outer cap exists because spinning a
        # second chromium-driven action on a 2GB NAS readily OOMs.
        async with _get_chromium_sem():
            sem = self._get_fetch_semaphore()
            async with sem:
                # Outer whole-operation cap (navigate + challenge clear +
                # extract). Navigation has its own ``page_load`` sub-cap below.
                return await asyncio.wait_for(
                    self._async_solve_locked(url, progress), timeout=timeout
                )

    async def _navigate_with_recycle(
        self, url: str, progress: ProgressCb | None = None
    ) -> object | None:
        """Open ``url`` in a new tab, recycling a wedged chromium once.

        On a resource-starved NAS the shared chromium occasionally wedges —
        ``browser.get`` then hangs the full ``page_load`` budget and the solve
        fails. A wedged instance won't recover by reusing it, so on the first
        nav timeout/error we tear it down and relaunch a fresh browser, then
        retry navigation once. Returns the page Tab, or None if both fail.
        """
        for attempt in (1, 2):
            browser = await self._ensure_shared_browser()
            with self._fetch_count_lock:
                self._fetch_count += 1
            log.info(
                "Navigating to %s for Cloudflare solve (pid %s, attempt %d)",
                url,
                self._shared_browser_pid,
                attempt,
            )
            _emit(
                progress,
                "Loading page in browser…"
                if attempt == 1
                else "Retrying page load in a fresh browser…",
            )
            try:
                return await asyncio.wait_for(
                    browser.get(url, new_tab=True),
                    timeout=self.config.page_load_timeout_seconds,
                )
            except Exception as exc:
                log.warning(
                    "Navigation to %s failed/timed out (attempt %d): %s",
                    url,
                    attempt,
                    exc,
                )
                if attempt == 1:
                    log.info("Recycling chromium after nav failure for %s", url)
                    _emit(progress, "Browser stalled; recycling…")
                    try:
                        await self._teardown_shared_browser()
                    except Exception:
                        log.debug("Recycle teardown failed", exc_info=True)
                    continue
                return None
        return None

    async def _async_solve_locked(
        self, url: str, progress: ProgressCb | None = None
    ) -> str | None:
        domain = self._domain_of(url)
        page = await self._navigate_with_recycle(url, progress)
        if page is None:
            log.warning(
                "CF navigation failed for %s (chromium unresponsive); no HTML", url
            )
            return None
        try:
            # Whole solve budget; the clearance loop + the cookie poll share it.
            loop = asyncio.get_event_loop()
            solve_deadline = loop.time() + self.config.solve_timeout_seconds
            # domain → keep re-engaging the Turnstile until cf_clearance lands,
            # not merely until the interstitial title flips. Success still means
            # "challenge cleared" (so JS-only-gated sites that issue no cookie
            # still return their HTML); the cookie chase is best-effort on top.
            cleared = await self._solve_challenge_on_page(
                page, url, domain=domain, progress=progress
            )
            if not cleared:
                # Still on the interstitial — returning its outerHTML would
                # masquerade as a successful fetch (non-empty string) and feed
                # the "Just a moment" page to the parser. Signal failure so the
                # caller can fall back to the cached-cookie curl_cffi path.
                log.warning("CF challenge did not clear for %s; returning no HTML", url)
                return None
            # Cache whatever cf_clearance landed so the NEXT request can skip
            # chromium and ride the cookie via curl_cffi until it expires.
            # Best-effort — failure here only costs a future chromium launch.
            try:
                await self._extract_and_cache_cookies(
                    page, domain, deadline=solve_deadline
                )
            except Exception:
                log.debug("Cookie harvest after solve failed", exc_info=True)
            try:
                html = await asyncio.wait_for(
                    page.evaluate("document.documentElement.outerHTML"),
                    timeout=30,
                )
            except Exception:
                log.exception("Failed to extract HTML from %s", url)
                return None
            if not isinstance(html, str):
                return None
            if self._is_blank_body(html):
                # Challenge cleared but the page rendered an empty <body> — the
                # site served nothing (blocked/parked for this IP or region;
                # seen on 1337x from some hosts). A non-empty-but-useless string
                # would read as a solve SUCCESS to the caller's breaker and make
                # every future request re-launch a browser tab forever. Treat as
                # failure so the breaker trips and stops the re-solve churn.
                log.warning(
                    "CF solve for %s cleared but body is empty "
                    "(site blocked/parked for this host?); returning no HTML",
                    url,
                )
                return None
            return html
        finally:
            await self._close_tab(page)

    @staticmethod
    def _is_blank_body(html: str) -> bool:
        """True when the rendered HTML has a ``<body>`` with no real content.

        A real scraper-target page always renders text in its body; an empty
        ``<body></body>`` means the navigation produced nothing usable. Kept
        conservative — only fires when a body tag is present and its inner
        content is whitespace after tags are stripped, so legitimate pages are
        never misclassified.
        """
        m = re.search(r"<body[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
        if m is None:
            return False
        inner = re.sub(r"<[^>]+>", "", m.group(1))
        return not inner.strip()

    async def await_ready(self, timeout: float = 60.0) -> bool:  # noqa: ASYNC109 — timeout forwarded to threading.Event.wait, part of public contract
        """Wait for the shared chromium to be live, up to ``timeout`` seconds.

        Returns True if the bypass is ready, False on timeout. Safe to call
        from any event loop — uses a threading.Event under the hood, so the
        caller's loop is never blocked (the wait runs in a thread pool).

        Intended use: gate lifespan startup tasks that touch CF-protected
        indexers so they don't kick off mid-warmup and all queue on the
        launch lock at once. Tasks remain correct without this — but the
        log gets cleaner.
        """
        if not self.config.enabled:
            return True
        if self._ready_event.is_set():
            return True
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._ready_event.wait, timeout)

    def warm(self, timeout: float | None = None) -> concurrent.futures.Future:
        """Pre-spawn the shared chromium so the first user-triggered search
        skips the 5-8s cold-start tax.

        Returns the concurrent.futures.Future from the worker loop so the
        caller can fire-and-forget or wait with their own timeout. Safe to
        call multiple times — re-entrant ``_ensure_shared_browser`` is a
        no-op once chromium is already up.

        Runs on the dedicated cf-bypass worker loop, NOT the caller's loop —
        nodriver registers its child-process watcher with the loop that
        first launches chromium and we want that to be the worker loop.

        No-op when the bypass is disabled — chromium is never spawned.
        """
        if not self.config.enabled:
            log.debug("Cloudflare bypass disabled; skipping warmup")
            fut: concurrent.futures.Future = concurrent.futures.Future()
            fut.set_result(None)
            return fut
        if self.config.uses_external_solver:
            # External HTTP solver: no local chromium to pre-spawn. Mark ready so
            # anything gated on ``await_ready`` proceeds immediately.
            log.debug(
                "Cloudflare external solver %r; skipping chromium warmup",
                self.config.solver_name,
            )
            self._ready_event.set()
            fut2: concurrent.futures.Future = concurrent.futures.Future()
            fut2.set_result(None)
            return fut2
        loop = self._ensure_worker_loop()

        async def _warm() -> None:
            # Wrap wait_for INSIDE the function so TimeoutError lands in our
            # try/except (otherwise the future just silently swallows it,
            # which is what masked our cancel-leak bug last round).
            try:
                self._ensure_display()
                if timeout is not None:
                    await asyncio.wait_for(
                        self._ensure_shared_browser(), timeout=timeout
                    )
                else:
                    await self._ensure_shared_browser()
                log.info(
                    "Cloudflare bypass warmup OK (shared chromium pid=%s)",
                    self._shared_browser_pid,
                )
            except TimeoutError:
                log.warning(
                    "Cloudflare bypass warmup timed out after %.0fs — will retry lazily on first solve/fetch",
                    timeout or 0.0,
                )
            except MissingChromiumError:
                # _start_browser already logged the actionable hint once. The
                # bypass stays enabled-but-inert; no traceback at boot.
                pass
            except Exception:
                log.exception(
                    "Cloudflare bypass warmup failed; will retry lazily on first solve/fetch"
                )

        return asyncio.run_coroutine_threadsafe(_warm(), loop)

    def shutdown(self) -> None:
        """Stop the worker loop + close shared chromium + reap stragglers.

        Wired into FastAPI's lifespan in ``miramedia.main`` so the dedicated
        thread + browser exit cleanly on app shutdown instead of being left
        dangling until the container is killed."""
        shared_browser = self._shared_browser
        shared_pid = self._shared_browser_pid
        self._shared_browser = None
        self._shared_browser_pid = None
        if shared_browser is not None:
            try:
                shared_browser.stop()
            except Exception:
                log.debug("Shared browser stop() failed", exc_info=True)
            if shared_pid:
                try:
                    CloudflareBypass._kill_chrome_tree(shared_pid)
                except Exception:
                    log.debug("Shared browser tree kill failed", exc_info=True)
        with self._worker_lock:
            loop = self._worker_loop
            thread = self._worker_thread
            self._worker_loop = None
            self._worker_thread = None
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                log.debug("Loop stop scheduling failed", exc_info=True)
        if thread is not None:
            thread.join(timeout=5)
        # Kill any other tracked PIDs we still own + drop tracked profile dirs.
        # Optional emergency blanket sweep gated by MIRAMEDIA_CHROMIUM_NUKE=1.
        try:
            self._kill_tracked_chrome()
            self._cleanup_tracked_profiles()
            self._nuke_all_chrome_if_enabled()
        except Exception:
            log.debug("Final chromium reap failed", exc_info=True)

    def _ensure_worker_loop(self) -> asyncio.AbstractEventLoop:
        """Lazy-start a dedicated asyncio worker thread + persistent loop.

        nodriver registers its child-process watcher with whichever loop
        first launched chromium. Closing that loop strands the watcher, and
        the next solve's brand-new loop has nothing to reap chromium with —
        which manifests as "Failed to connect to browser". Keeping a single
        loop alive for the process lifetime avoids the stale-watcher
        cascade.
        """
        with self._worker_lock:
            if self._worker_loop is not None and not self._worker_loop.is_closed():
                return self._worker_loop

            ready = threading.Event()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._worker_loop = loop
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._worker_thread = threading.Thread(
                target=_run, name="cf-bypass-loop", daemon=True
            )
            self._worker_thread.start()
            ready.wait(timeout=5)
            if self._worker_loop is None:
                msg = "cf-bypass worker event loop failed to start within 5s"
                raise RuntimeError(msg)
            return self._worker_loop

    def _remote_endpoint(self) -> tuple[str, int] | None:
        """``(host, port)`` to connect to when ``solver = "remote"``, else None.

        Parses ``[cloudflare.remote].endpoint`` (``http://host:9222``). When
        set, the native solve loop attaches to that already-running Chrome
        instead of launching one locally — the browser lives on a real-GPU box
        so WebGL fingerprints as a real device.
        """
        if self.config.solver_name != "remote":
            return None
        endpoint = (self.config.remote.endpoint or "").strip()
        if not endpoint:
            return None
        from urllib.parse import urlparse

        parsed = urlparse(endpoint if "//" in endpoint else f"http://{endpoint}")
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            log.error(
                "Cloudflare remote solver endpoint %r missing host/port; "
                "expected http://host:9222",
                endpoint,
            )
            return None
        return host, port

    async def _start_browser(self) -> object:
        import tempfile

        import nodriver as uc

        # ``remote`` solver: connect to a Chrome already running on another
        # machine (real GPU → real WebGL fingerprint). nodriver skips the local
        # launch entirely when host+port are both supplied. No profile dir, no
        # flags, no Xvfb, and crucially no PID to track — teardown/recycle must
        # never kill a browser we didn't spawn.
        remote = self._remote_endpoint()
        if remote is not None:
            host, port = remote
            log.info(
                "Cloudflare bypass: connecting to remote browser %s:%d", host, port
            )
            # connect-existing: nodriver never launches a local browser, but
            # Config still resolves an executable at construction and RAISES if
            # none is found. Hand it the configured path (or a harmless
            # placeholder that's never executed in connect mode) so the app host
            # needs no local Chrome — the whole point of remote mode.
            return await uc.start(
                host=host,
                port=port,
                browser_executable_path=self.config.browser_path or "chrome",
            )

        # Local native launch. The slim default image ships no chromium binary
        # (it lives only in the ``-cf`` variant), so check before launch and
        # fail with one actionable WARNING instead of nodriver's raw
        # FileNotFoundError on every attempt. Only relevant when relying on
        # autodetect — an explicit ``browser_path`` is trusted as-is.
        if not self.config.browser_path:
            from nodriver.core.config import find_chrome_executable

            try:
                find_chrome_executable()
            except FileNotFoundError:
                if not self._missing_chromium_warned:
                    self._missing_chromium_warned = True
                    log.warning(
                        "Cloudflare bypass enabled but no Chromium found in this "
                        "image. Use the 'miramedia:latest-cf' image variant, set "
                        "[cloudflare] browser_path, or switch to an external "
                        "solver (byparr/flaresolverr/remote/browser_run/firecrawl)."
                    )
                raise MissingChromiumError from None

        # Upstream nodriver waits only 2.5s (range(5) * 0.5s) for chromium's
        # DevTools port to come up. Warm-cache restarts in containerized
        # cloudflare-bypass loops can take 4-8s+, after which nodriver raises
        # the misleading "Failed to connect to browser ... running as root"
        # error. Runtime monkey-patch the constant so we don't depend on a
        # source-edit in the Dockerfile (which is brittle across upgrades).
        self._patch_nodriver_connect_retries(
            uc, launch_timeout_seconds=self.config.browser_launch_timeout_seconds
        )

        # Optional emergency cleanup gated by env var. Off by default — the
        # broad sweep used to clobber siblings (sibling chromiums spawned by
        # solve while fetch was launching, etc.).
        self._nuke_all_chrome_if_enabled()

        # Fresh per-call profile dir. Tracked so teardown removes only our own.
        profile_dir = tempfile.mkdtemp(prefix="nodriver-", dir="/tmp")
        self._track_chromium(pid=None, profile_dir=profile_dir)

        args = {
            "headless": False,
            "sandbox": False,
            "user_data_dir": profile_dir,
            "browser_executable_path": self.config.browser_path or None,
            "browser_args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                # Resource-pressure stability flags for Synology / low-RAM
                # NAS hosts. Without ``--in-process-network-service`` the
                # NetworkService subprocess crashes under memory pressure
                # and the DevTools HTTP responder wedges (TCP accept, no
                # response) — 120s probe burns waiting on dead handler.
                "--in-process-network-service",
                # NB: do NOT add ``--disable-software-rasterizer`` — combined
                # with ``--disable-gpu`` it leaves chromium with NO GL backend,
                # so ``getContext('webgl')`` returns null and WebGL fingerprints
                # as absent. A browser with zero WebGL renderer is a glaring bot
                # signal to Cloudflare (orchestrator silently parks us on the
                # "verifying you are not a bot" page and never issues Turnstile).
                # Force the SwiftShader software GL backend so WebGL reports a
                # real renderer string, as a GPU-less VM/desktop would.
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-ipc-flooding-protection",
                "--disable-features=Translate,OptimizationHints,MediaRouter,DialMediaRouteProvider,CalculateNativeWinOcclusion,InterestFeedContentSuggestions",
                "--no-default-browser-check",
                "--mute-audio",
                # Memory: stop chromium pre-allocating render-process pools.
                "--renderer-process-limit=2",
            ],
        }

        async def _launch_once() -> tuple[object, int | None]:
            browser = await uc.start(**args)
            proc = getattr(browser, "_process", None) or getattr(
                browser, "process", None
            )
            pid = proc.pid if proc else None
            self._track_chromium(pid=pid, profile_dir=None)
            return browser, pid

        def _cleanup_partial(profile: str) -> None:
            """Kill any chromium process tree spawned with our profile dir
            and remove the dir. Called on launch failure OR cancellation —
            chromium may have been forked before the error/cancel hit, and
            without this we leak entire browser instances.
            """
            try:
                CloudflareBypass._kill_chromium_by_profile(profile)
            except Exception:
                log.debug("Profile-targeted kill failed", exc_info=True)
            self._forget_chromium(pid=None, profile_dir=profile)
            shutil.rmtree(profile, ignore_errors=True)

        # Two attempts max: original + one retry on plain Exception.
        # CancelledError aborts immediately (no orphans left behind).
        for attempt in range(2):
            try:
                browser, _pid = await _launch_once()
                return browser  # noqa: TRY300 — must stay in try; preceding _launch_once() is guarded by the except below
            except asyncio.CancelledError:
                log.warning(
                    "Browser launch cancelled on attempt %d; cleaning up profile %s",
                    attempt + 1,
                    profile_dir,
                )
                _cleanup_partial(profile_dir)
                raise
            except Exception as err:
                _cleanup_partial(profile_dir)
                if attempt == 0:
                    log.warning(f"Browser launch failed ({err!r}), retrying once...")
                    profile_dir = tempfile.mkdtemp(prefix="nodriver-", dir="/tmp")
                    self._track_chromium(pid=None, profile_dir=profile_dir)
                    args["user_data_dir"] = profile_dir
                    await asyncio.sleep(2)
                    continue
                raise
        # Unreachable — both attempts either return or raise.
        msg = "unreachable: _start_browser loop exited without result"
        raise RuntimeError(msg)

    @staticmethod
    async def _human_mouse_click(page, x: int, y: int) -> None:  # noqa: ANN001
        """Move the pointer to (x, y) along a jittered, multi-step path, dwell,
        then press+release. Cloudflare Turnstile scores pointer entropy — an
        instant teleport-click (dispatch a single press at the target) reads as
        a bot and the challenge never issues cf_clearance, even though the
        checkbox is technically 'clicked'. Stepping the move with small random
        offsets + delays is the minimum humanization that makes the interactive
        widget yield (the in-stack equivalent of a 'humanize' flag).
        """
        import random

        import nodriver.cdp.input_ as cdp_input

        # Start a bit up-and-left of the target, like a real cursor approaching.
        start_x = x - random.randint(60, 140)  # noqa: S311 — non-cryptographic humanization jitter
        start_y = y - random.randint(40, 100)  # noqa: S311 — non-cryptographic humanization jitter
        # Keep step count modest: each step is a CDP round-trip, and on a
        # CPU/IO-starved NAS those add up fast. ~10-14 still gives Turnstile
        # enough pointer entropy without risking the per-attempt timeout.
        steps = random.randint(10, 14)  # noqa: S311 — non-cryptographic humanization jitter
        for i in range(1, steps + 1):
            t = i / steps
            # Ease-out so motion decelerates into the target, plus per-step
            # jitter that fades as we close in.
            ease = 1 - (1 - t) ** 2
            jitter = (1 - t) * 6
            px = start_x + (x - start_x) * ease + random.uniform(-jitter, jitter)  # noqa: S311 — non-cryptographic humanization jitter
            py = start_y + (y - start_y) * ease + random.uniform(-jitter, jitter)  # noqa: S311 — non-cryptographic humanization jitter
            await asyncio.wait_for(
                page.send(
                    cdp_input.dispatch_mouse_event(
                        type_="mouseMoved",
                        x=float(px),
                        y=float(py),
                    )
                ),
                timeout=8,
            )
            await asyncio.sleep(random.uniform(0.008, 0.03))  # noqa: S311 — non-cryptographic humanization jitter

        # Dwell before pressing, then a short press duration.
        await asyncio.sleep(random.uniform(0.05, 0.18))  # noqa: S311 — non-cryptographic humanization jitter
        await asyncio.wait_for(
            page.send(
                cdp_input.dispatch_mouse_event(
                    type_="mousePressed",
                    x=float(x),
                    y=float(y),
                    button=cdp_input.MouseButton.LEFT,
                    click_count=1,
                )
            ),
            timeout=8,
        )
        await asyncio.sleep(random.uniform(0.04, 0.12))  # noqa: S311 — non-cryptographic humanization jitter
        await asyncio.wait_for(
            page.send(
                cdp_input.dispatch_mouse_event(
                    type_="mouseReleased",
                    x=float(x),
                    y=float(y),
                    button=cdp_input.MouseButton.LEFT,
                    click_count=1,
                )
            ),
            timeout=8,
        )

    @staticmethod
    async def _solve_via_keyboard(page) -> None:  # noqa: ANN001 — nodriver Tab imported lazily, no static type available
        import nodriver.cdp.input_ as cdp_input

        async def press_key(key: str, code: str, vk: int) -> None:
            await asyncio.wait_for(
                page.send(
                    cdp_input.dispatch_key_event(
                        type_="keyDown",
                        key=key,
                        code=code,
                        windows_virtual_key_code=vk,
                        native_virtual_key_code=vk,
                    )
                ),
                timeout=8,
            )
            await asyncio.sleep(0.05)
            await asyncio.wait_for(
                page.send(
                    cdp_input.dispatch_key_event(
                        type_="keyUp",
                        key=key,
                        code=code,
                        windows_virtual_key_code=vk,
                        native_virtual_key_code=vk,
                    )
                ),
                timeout=8,
            )

        # PRIMARY: keyboard TAB+SPACE+Enter. This is the path that clears the
        # widget reliably in practice (the closed cross-origin shadow DOM takes
        # keyboard focus), so run it EVERY attempt — don't gate it behind the
        # mouse click. Cheap (no coords needed) and works headless/headful.
        try:
            log.debug("Attempting Turnstile bypass via keyboard TAB+SPACE")
            for num_tabs in (1, 2, 3):
                for _ in range(num_tabs):
                    await press_key("Tab", "Tab", 9)
                    await asyncio.sleep(0.15)

                await press_key(" ", "Space", 32)
                await asyncio.sleep(0.3)
                await press_key("Enter", "Enter", 13)
                await asyncio.sleep(0.5)
        except (TimeoutError, Exception):
            log.debug("Keyboard bypass attempt failed", exc_info=True)

        # SUPPLEMENT: humanized mouse move + click on the Turnstile checkbox
        # (~30px in from the iframe's left edge, vertically centred). Helps the
        # cases where keyboard focus doesn't reach the widget. Readiness-gated
        # so we never teleport to bad coords before the iframe renders.
        try:
            # Return a flat JS ARRAY, not an object — nodriver's evaluate
            # deserializes a JS object to a list of pairs (``.get`` then blows
            # up); an array comes back as a clean Python list we can index.
            # [present(0/1), x, y, w, h].
            rect = await asyncio.wait_for(
                page.evaluate(
                    """
                    (() => {
                        const iframe = document.querySelector(
                            'iframe[src*="challenges.cloudflare.com"]'
                        );
                        if (!iframe) return [0, 0, 0, 0, 0];
                        const r = iframe.getBoundingClientRect();
                        return [1, r.x, r.y, r.width, r.height];
                    })()
                    """,
                    return_by_value=True,
                ),
                timeout=12,
            )
            vals = list(rect) if isinstance(rect, (list, tuple)) else []
            present = bool(vals and vals[0])
            rx = float(vals[1]) if len(vals) > 1 else 0.0
            ry = float(vals[2]) if len(vals) > 2 else 0.0
            w = float(vals[3]) if len(vals) > 3 else 0.0
            h = float(vals[4]) if len(vals) > 4 else 0.0
            if present and w >= 10 and h >= 10:
                # Widget rendered + clickable. We run AFTER the keyboard solve,
                # so a rendered widget here means keyboard didn't clear it this
                # round. Debug — the cleared/cached lines are the real signal.
                log.debug(
                    "Turnstile widget present (size=%.0fx%.0f); humanized click", w, h
                )
                x = int(rx + 30)
                y = int(ry + h / 2)
                await CloudflareBypass._human_mouse_click(page, x, y)
            else:
                # No interactable iframe. This is the COMMON success path: the
                # keyboard solve (which ran first) already cleared the challenge,
                # so the Turnstile iframe is gone. Also covers "not rendered yet"
                # on a slow box. Either way nothing to click — debug, not info,
                # so it stops reading like a failure.
                log.debug(
                    "Turnstile iframe absent (cleared or not yet rendered); "
                    "no click this round"
                )
        except (TimeoutError, Exception):
            log.debug("Humanized mouse click failed", exc_info=True)

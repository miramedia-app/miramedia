"""Web Vitals + ops endpoints. Lightweight — full metrics live at /metrics."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Literal

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Request, status
from prometheus_client import REGISTRY, Histogram
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"], include_in_schema=False)


# Tight pydantic constraints — vitals payloads are tiny (<200 bytes). Cheap DoS
# backstop: oversized strings get rejected before any business logic runs.
class WebVital(BaseModel):
    name: Literal["CLS", "FCP", "FID", "INP", "LCP", "TTFB"]
    value: Annotated[float, Field(ge=0, le=1e9)]
    id: Annotated[str, Field(max_length=64)]
    rating: Literal["good", "needs-improvement", "poor"] | None = None
    navigationType: Annotated[str | None, Field(max_length=32)] = None  # noqa: N815 — matches web-vitals JS payload key


class PerformanceBudget(BaseModel):
    metric: str
    good: float
    poor: float
    unit: str


# Per-metric Prometheus histograms. Buckets follow the official
# Web Vitals "good / needs improvement / poor" thresholds so percentile
# alerts can be wired directly against published guidance:
#   https://web.dev/articles/vitals
# Values are in ms for timing metrics, unitless ratio for CLS.
_VITAL_BUCKETS: dict[str, tuple[float, ...]] = {
    "LCP": (500, 1000, 2500, 4000, 8000),
    "INP": (50, 100, 200, 500, 1000),
    "CLS": (0.05, 0.1, 0.25, 0.5, 1.0),
    "FCP": (500, 1000, 1800, 3000, 6000),
    "TTFB": (100, 200, 500, 800, 1800),
    "FID": (50, 100, 300, 600, 1000),
}

_BUDGETS = {
    "LCP": PerformanceBudget(metric="LCP", good=2500, poor=4000, unit="ms"),
    "INP": PerformanceBudget(metric="INP", good=200, poor=500, unit="ms"),
    "CLS": PerformanceBudget(metric="CLS", good=0.1, poor=0.25, unit="ratio"),
    "TTFB": PerformanceBudget(metric="TTFB", good=800, poor=1800, unit="ms"),
}


def _make_histogram(name: str, buckets: tuple[float, ...]) -> Histogram:
    """Create or reuse the histogram for ``name``.

    Module-level histogram registration trips ``ValueError: Duplicated
    timeseries`` on dev reload / test re-import because the global
    ``CollectorRegistry`` is process-wide. On collision we look up and return
    the existing collector instead of raising. ``REGISTRY._names_to_collectors``
    is a private attribute — wrapped defensively because prometheus-client does
    not expose a public lookup API.
    """
    metric_name = f"web_vital_{name.lower()}"
    try:
        return Histogram(
            metric_name,
            f"Browser-reported {name} web vital",
            labelnames=["rating"],
            buckets=buckets,
        )
    except ValueError:
        existing = getattr(REGISTRY, "_names_to_collectors", {}).get(metric_name)
        if existing is None:
            raise
        return existing  # type: ignore[return-value]


_histograms: dict[str, Histogram] = {
    name: _make_histogram(name, buckets) for name, buckets in _VITAL_BUCKETS.items()
}


# Per-IP rate limit: 60 reports/min default is generous (≈5 vitals × 10 page loads).  # noqa: RUF003 — multiplication sign intentional
# Per-process counter — under multi-worker the effective ceiling multiplies by
# worker count. Acceptable for vitals (non-security-critical); the perimeter WAF
# / reverse proxy remains the primary defense against abuse.
_RATE_LIMIT_PER_MIN = int(os.getenv("MIRAMEDIA_VITALS_RATE_LIMIT_PER_MIN", "60"))
_RATE_WINDOW_S = 60
_rate_counts: TTLCache[str, int] = TTLCache(maxsize=4096, ttl=_RATE_WINDOW_S)


def _check_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    current = _rate_counts.get(ip, 0)
    if current >= _RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    _rate_counts[ip] = current + 1


@router.post("/vitals", status_code=204)
async def report_vital(vital: WebVital, request: Request) -> None:
    """Receive a web-vitals beacon. Pushed to a Prometheus histogram so
    /metrics can serve p50/p95/p99 percentiles; also logged inline so the
    raw stream is grep-able without a Prometheus stack.

    Logged at DEBUG, not INFO: the SPA emits ~5 beacons per page load and the
    ``DatabaseLogHandler`` persists every ``miramedia.*`` INFO record, so INFO
    here wrote 5 activity_log rows per pageview — bloating the table and the
    /logs page that polls it. The histogram is the real sink; DEBUG keeps the
    grep-able line in development without flooding production storage.
    """
    _check_rate(request)
    rating = vital.rating or "unknown"
    hist = _histograms.get(vital.name)
    if hist is not None:
        hist.labels(rating=rating).observe(vital.value)
    log.debug(
        "web_vital %s=%.3f rating=%s nav=%s id=%s",
        vital.name,
        vital.value,
        rating,
        vital.navigationType or "n/a",
        vital.id,
    )


@router.get("/budgets")
async def get_performance_budgets() -> list[PerformanceBudget]:
    """Expose the browser performance budgets the app records against."""
    return list(_BUDGETS.values())

"use client";

import { useEffect } from "react";

// Backend route is mounted under /api/v1/ (see miramedia/main.py). If you
// move the analytics route, update this constant.
const ENDPOINT = "/api/v1/analytics/vitals";

interface VitalMetric {
  name: string;
  value: number;
  id: string;
  rating?: string;
  navigationType?: string;
}

// Reports Core Web Vitals (CLS, FCP, INP, LCP, TTFB) to the backend once per
// metric per page load. Uses sendBeacon when available so the request
// survives a page unload, with a keepalive fetch fallback for older
// browsers. The web-vitals import is dynamic so the library only ships in
// the chunk when this component actually mounts.
export function WebVitals() {
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { onCLS, onFCP, onINP, onLCP, onTTFB } = await import("web-vitals");
        const send = (m: VitalMetric) => {
          if (cancelled) return;
          const body = JSON.stringify({
            name: m.name,
            value: m.value,
            id: m.id,
            rating: m.rating ?? null,
            navigationType: m.navigationType ?? null,
          });
          if (typeof navigator !== "undefined" && "sendBeacon" in navigator) {
            const blob = new Blob([body], { type: "application/json" });
            navigator.sendBeacon(ENDPOINT, blob);
          } else {
            void fetch(ENDPOINT, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body,
              keepalive: true,
            }).catch(() => {});
          }
        };
        onCLS(send);
        onFCP(send);
        onINP(send);
        onLCP(send);
        onTTFB(send);
      } catch {
        // web-vitals failed to load — silently ignore; this is opt-in
        // telemetry, not a critical path.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}

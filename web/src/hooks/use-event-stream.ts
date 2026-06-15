"use client";

import { useEffect, useRef } from "react";

type EventHandler = (data: unknown, type: string) => void;

interface Options {
  /** Path relative to the API base. Defaults to "/api/v1/events/stream". */
  url?: string;
  /** Map of SSE event name to handler. */
  handlers: Record<string, EventHandler>;
  /** Pause subscription when false (e.g. tab off-screen). Defaults true. */
  enabled?: boolean;
}

/**
 * Subscribe to a server-sent event stream. Auto-reconnects with exponential
 * backoff (1s → 16s cap). Browser EventSource handles ping comments
 * transparently, so the 15s server-side heartbeat needs no special handling.
 *
 * Auth: relies on cookies (backend mounts cookie auth at
 * `/api/v1/auth/cookie`). `withCredentials` matches the openapi-fetch
 * client's `credentials: "include"` mode in `src/lib/api/client.ts`.
 */
export function useEventStream({
  url = "/api/v1/events/stream",
  handlers,
  enabled = true,
}: Options) {
  // Stash handlers in a ref so the EventSource isn't recreated on every
  // render when callers pass inline objects/closures.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!enabled) return;

    // openapi-fetch is configured with `baseUrl: process.env.NEXT_PUBLIC_API_URL`
    // so SSE must hit the same origin to keep cookies in scope.
    const base = process.env.NEXT_PUBLIC_API_URL || "";
    const fullUrl = url.startsWith("http") ? url : `${base}${url}`;

    let es: EventSource | null = null;
    let retryDelay = 1000;
    let stopped = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      es = new EventSource(fullUrl, { withCredentials: true });

      es.onopen = () => {
        retryDelay = 1000;
      };

      es.onerror = () => {
        if (stopped) return;
        es?.close();
        es = null;
        // Backoff: 1s → 2s → 4s → 8s → 16s cap.
        retryTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 16000);
      };

      // Bind every handler that was registered at mount. We read from the
      // ref inside the listener so updated handlers are always picked up
      // without reconnecting.
      for (const type of Object.keys(handlersRef.current)) {
        es.addEventListener(type, (ev: MessageEvent) => {
          let payload: unknown = ev.data;
          try {
            payload = JSON.parse(ev.data);
          } catch {
            // Not JSON; pass the raw string through.
          }
          handlersRef.current[type]?.(payload, type);
        });
      }
    };

    connect();

    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
    // handlers identity intentionally excluded — we read latest via ref.
  }, [url, enabled]);
}

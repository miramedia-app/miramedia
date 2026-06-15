"use client";

import { useParams, usePathname } from "next/navigation";

const UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g;

/**
 * Read a dynamic UUID segment from the URL, even when a dev/prod rewrite has
 * masked the original URL into `_shell` (see `next.config.ts` rewrites and
 * the FastAPI 404 handler). `useParams()` returns `_shell` in that case; we
 * fall back to extracting the UUID from `usePathname()`.
 */
export function useRouteUuid(paramName: string, index = 0): string | undefined {
  const params = useParams<Record<string, string | string[]>>();
  const pathname = usePathname();
  const raw = params?.[paramName];
  const param = Array.isArray(raw) ? raw[0] : raw;
  if (param && param !== "_shell") return param;
  const matches = pathname.match(UUID_RE);
  return matches?.[index];
}

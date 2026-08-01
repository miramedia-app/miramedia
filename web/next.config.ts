import type { NextConfig } from "next";
import { createMDX } from "fumadocs-mdx/next";

const basePath = process.env.BASE_PATH || "";

// Honor NEXT_PUBLIC_* if set directly (dev compose); otherwise fall back to
// legacy PUBLIC_* names baked at build time by the Dockerfile.
const apiUrl = process.env.NEXT_PUBLIC_API_URL || process.env.PUBLIC_API_URL || "";
const version = process.env.NEXT_PUBLIC_VERSION || process.env.PUBLIC_VERSION || "dev";

// Only enable static export for production builds. Dev mode keeps the regular
// dev server so dynamic params resolve without needing FastAPI's catch-all.
const isProd = process.env.NODE_ENV === "production";

const UUID = ":uuid([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})";

const nextConfig: NextConfig = {
  ...(isProd ? { output: "export" as const } : {}),
  basePath,
  trailingSlash: true,
  // Disable the Next dev server's gzip. It otherwise re-compresses the
  // proxied /api/* responses (see rewrites below) — including the SSE search
  // stream — and gzip buffering collapses every incremental chunk to the end,
  // so streamed search results only appear once the slowest backend finishes.
  // The backend already excludes text/event-stream from its own gzip; Next was
  // overriding that. No prod impact: `output: 'export'` runs no Next server and
  // FastAPI handles compression. See node_modules/next/dist/.../compress.md.
  compress: false,
  images: { unoptimized: true },
  experimental: {
    optimizePackageImports: ["lucide-react", "fumadocs-ui", "fumadocs-core"],
    // Idle timeout (ms) on the dev rewrite proxy's socket to the backend —
    // reset on every byte, NOT a total-request cap. The Next default is 30s;
    // we use 60s, the de-facto gateway standard (nginx proxy_read_timeout, AWS
    // ALB idle). Long-lived SSE endpoints (search / events / indexer-test
    // streams) stay alive regardless: sse-starlette pings every 15s, well under
    // this, so the idle timer never fires mid-stream. Dev-only — prod uses
    // `output: 'export'` with no Next server.
    proxyTimeout: 60_000,
  },
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_API_URL: apiUrl,
    NEXT_PUBLIC_VERSION: version,
  },
  // Dev-only rewrites: UUID detail paths -> SPA shell route. Use `beforeFiles`
  // so the rewrite fires before Next's `dynamicParams: false` 404 check.
  // Also proxies /api/* and /openapi.json to the backend so direct :5555
  // access works without CORS gymnastics. Target defaults to the
  // compose-internal `api:8000`; override with NEXT_PUBLIC_DEV_API_PROXY
  // for non-compose setups.
  // Prod uses the FastAPI 404 handler instead (rewrites disallowed under
  // `output: 'export'`).
  ...(isProd
    ? {}
    : {
        async rewrites() {
          const devApiTarget = process.env.NEXT_PUBLIC_DEV_API_PROXY || "http://api:8000";
          return {
            beforeFiles: [
              { source: `/dashboard/shows/${UUID}`, destination: "/dashboard/shows/_shell" },
              {
                source: `/dashboard/shows/${UUID}/${UUID.replace(":uuid", ":season")}`,
                destination: "/dashboard/shows/_shell/_shell",
              },
              { source: `/dashboard/movies/${UUID}`, destination: "/dashboard/movies/_shell" },
              { source: "/api/:path*", destination: `${devApiTarget}/api/:path*` },
              { source: "/openapi.json", destination: `${devApiTarget}/openapi.json` },
            ],
            afterFiles: [],
            fallback: [],
          };
        },
      }),
};

const withMDX = createMDX();

export default withMDX(nextConfig);

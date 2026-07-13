FROM node:24-alpine AS frontend-build
WORKDIR /frontend

RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./

# Same prerequisites as `make frontend-generate`: the app imports the generated
# Fumadocs collections (.source) and Next type declarations, and both are
# gitignored, so the image must generate them rather than inherit them from the
# build context. pnpm 10 blocks postinstall scripts, so this cannot be implicit.
RUN pnpm exec fumadocs-mdx && pnpm exec next typegen

ARG VERSION
ARG BASE_PATH=""
RUN env PUBLIC_VERSION=${VERSION} PUBLIC_API_URL=${BASE_PATH} BASE_PATH=${BASE_PATH} pnpm build
RUN node - <<'NODE'
const { brotliCompressSync, gzipSync, constants } = require('node:zlib');
const { readdirSync, readFileSync, writeFileSync, statSync } = require('node:fs');
const { join } = require('node:path');
const root = '/frontend/out';
const exts = new Set(['.html', '.js', '.css', '.json', '.svg', '.txt', '.xml']);
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const st = statSync(path);
    if (st.isDirectory()) walk(path);
    else {
      const ext = name.slice(name.lastIndexOf('.'));
      if (!exts.has(ext) || st.size < 1024) continue;
      const input = readFileSync(path);
      writeFileSync(path + '.br', brotliCompressSync(input, {
        params: { [constants.BROTLI_PARAM_QUALITY]: 11 },
      }));
      writeFileSync(path + '.gz', gzipSync(input, { level: 9 }));
    }
  }
}
walk(root);
NODE

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS base

# BuildKit cache mounts keep apt's download cache + package list across rebuilds
# without bloating the image layer. ``rm -f /etc/apt/apt.conf.d/docker-clean``
# is required because the base image installs a hook that wipes
# /var/cache/apt/archives after every install — that hook fights the cache mount.
# ``gcc`` is intentionally NOT installed here: every Python dep we use ships
# binary wheels for cp313 linux (libtorrent, psycopg[binary], argon2-cffi).
RUN rm -f /etc/apt/apt.conf.d/docker-clean

# The Chromium stack (chromium + fonts + Xvfb + dbus) is NOT installed in this
# base/slim image — it lives only in the ``app-cf`` final stage so the default
# image stays small. Cloudflare native-solver users build/pull the ``-cf``
# variant; everyone else (most indexers need no CF bypass) saves ~hundreds of MB.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates bash libtorrent21 bc locales postgresql media-types \
        mailcap curl gzip unzip tar 7zip bzip2 unar gosu ffmpeg

RUN sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
RUN locale-gen
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Create a non-root user and group
RUN groupadd -g 1000 miramedia && \
    useradd -m -u 1000 -g miramedia miramedia

FROM base AS dependencies
WORKDIR /app
# Ensure miramedia owns /app
RUN chown -R miramedia:miramedia /app

USER miramedia

ENV UV_CACHE_DIR=/home/miramedia/.cache/uv \
    UV_LINK_MODE=copy

COPY --chown=miramedia:miramedia pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/home/miramedia/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked --no-dev

FROM dependencies AS app
ARG VERSION
ARG BASE_PATH=""
LABEL author="github.com/miramedia-app"
LABEL version=${VERSION}
LABEL description="Docker image for MiraMedia"
USER root

# uv run must use the baked venv (no dev-group re-sync / network at boot)
ENV UV_NO_SYNC=1 \
    PUBLIC_VERSION=${VERSION} \
    MIRAMEDIA_CONFIG_DIR="/app/config" \
    BASE_PATH=${BASE_PATH} \
    FRONTEND_FILES_DIR="/app/web/build"

COPY --chown=miramedia:miramedia --chmod=755 scripts/startup.sh /app/startup.sh
COPY --chown=miramedia:miramedia config.example.toml /app/config.example.toml
COPY --chown=miramedia:miramedia miramedia ./miramedia
COPY --chown=miramedia:miramedia alembic ./alembic
COPY --chown=miramedia:miramedia alembic.ini .

# Next.js static export — baked into the app image so FastAPI's
# CachedStaticFiles mount in main.py can serve it directly. Used by all
# deployments (prod + dev both build `--target app`); dev mounts source
# code over /app/miramedia so this baked frontend is only relevant when
# FastAPI serves it. Removing Next's static 404.html so the SPA router
# can claim unknown paths via FastAPI's 404 handler fallback.
COPY --chown=miramedia:miramedia --from=frontend-build /frontend/out /app/web/build
RUN rm -f /app/web/build/404.html

# No ${BASE_PATH} prefix: BASE_PATH is passed to FastAPI as root_path (proxy
# metadata only) — the app still serves health at /api/v1/health, so prefixing
# here 404s and marks the container permanently unhealthy. start-period covers
# a cold NAS doing migrate + libtorrent resume restore before uvicorn listens.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1
EXPOSE 8000
CMD ["/app/startup.sh"]

# ``app-cf`` = the opt-in Cloudflare variant. It layers the full Chromium stack
# on top of the finished slim ``app`` image so the native solver can launch a
# headful browser. Build/pull this tag (e.g. miramedia:latest-cf) only when
# running ``[cloudflare] enabled = true`` with ``solver = "native"``. External
# solvers (byparr/flaresolverr/remote/browser_run/firecrawl) need no local
# chromium and run fine on the slim image.
#
# Inherits the app stage's ``USER root`` — startup.sh drops privileges via gosu
# at runtime, so the final USER is unchanged between variants.
FROM app AS app-cf
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium chromium-common \
        fonts-noto-cjk fonts-noto-color-emoji xvfb dbus-x11 \
        fonts-liberation fonts-liberation2 fonts-crosextra-carlito \
        fonts-crosextra-caladea

# Xvfb needs /tmp/.X11-unix to exist before it starts. xtrans refuses to
# mkdir+chown it when euid != 0, so create it ahead of time with the
# standard sticky world-writable perms so any non-root container user
# can drop its display socket inside.
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

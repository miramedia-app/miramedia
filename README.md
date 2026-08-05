<br />
<div align="center">
  <a href="https://miramedia-app.github.io/miramedia/">
    <img src="web/public/logo.svg" alt="MiraMedia" width="120">
  </a>

<h3 align="center">MiraMedia</h3>

  <p align="center">
    Modern management system for your media library
    <br />
    <a href="https://miramedia-app.github.io/miramedia/"><strong>Explore the docs »</strong></a>
    <br />
    <a href="https://github.com/miramedia-app/miramedia/issues/new?labels=bug&template=bug_report.md">Report Bug</a>
    &middot;
    <a href="https://github.com/miramedia-app/miramedia/issues/new?template=feature_request.md">Request Feature</a>
  </p>
</div>


MiraMedia is the modern, easy-to-use successor to the fragmented "Arr" stack. Manage, discover, and automate your TV and movie collection in a single, simple interface.

**Works out of the box with zero external services.** The native metadata
provider, indexer, download client, and subtitle search are all enabled by
default — no Prowlarr, qBittorrent, Sonarr/Radarr, or API keys required to
get started. Plug in external services later if you want them.

### Key features

**Discovery & metadata**
- Native metadata provider (TVmaze + Cinemeta) — no API key needed; optional TMDB / TVDB
- IMDb-ID-based identification and folder naming, fully templated naming scheme
- Configurable media libraries (split shows/movies across multiple roots)

**Acquisition**
- Native torrent indexer with 7 preloaded sites (YTS, EZTV, TPB, 1337x, TorrentGalaxy, LimeTorrents, Nyaa); add custom Torznab/private trackers
- Optional external indexers: Prowlarr, Jackett
- Native libtorrent download client; or qBittorrent / Transmission / SABnzbd
- Unified torrent search & download across TV and movies with a configurable scoring engine (quality/codec/title/flag rules, per-library rulesets)
- Manual add via magnet link or .torrent file upload with fuzzy media matching
- Continuous download: auto-grab missing episodes/movies (global or per-title)
- Shared Cloudflare bypass (in-process nodriver + curl_cffi) for protected sites

**Import & library management**
- Robust import pipeline with `guessit` filename parsing + mediainfo analysis
- Safe archive extraction for imports: ZIP, TAR, TAR.GZ, TAR.BZ2, GZIP, and BZIP2
  are supported; RAR, 7z, FreeArc, TAR.XZ, and ZIP64 are intentionally rejected
  because they are not bounded by the fail-closed parser policy (convert these
  archives to a supported format before import)
- Per-file import status with automatic retry (exponential backoff)
- Import recovery dashboard: inspect, remap, retry, or ignore failed/ambiguous imports
- Library scanner to ingest existing on-disk media

**Playback & extras**
- In-browser streaming (original-file HTTP range playback, with WebCodecs/Mediabunny fallback when native decode fails)
- Subtitle management via native search (subliminal + first-party subdl/subsource/yifysubtitles, vendored keyless plugins, or optional Bazarr); SRT auto-converted to WebVTT for playback
- Optional media request system (native fulfillment and/or forward to Overseerr/Jellyseerr)

**Operations**
- OAuth/OIDC + email-password auth, dashboard-managed superusers
- Notifications: email, Pushover, Gotify, Ntfy
- Database-backed system logs with filtering, viewable in-app
- In-app update checks with optional one-click apply via the Docker socket
- Designed to be deployed with Docker

## Quick Start

```sh
wget -O docker-compose.yaml https://github.com/miramedia-app/miramedia/releases/latest/download/docker-compose.yaml
mkdir config
wget -O ./config/config.toml https://github.com/miramedia-app/miramedia/releases/latest/download/config.example.toml
# you probably need to edit the config.toml file in the ./config directory, for more help see the documentation
docker compose up -d
```

### [View the docs for installation instructions and more](https://miramedia-app.github.io/miramedia/)

## Updating

MiraMedia checks GitHub for new releases and shows an in-app banner when one
is available. Superusers can see release notes at
**Dashboard → System → Updates** and run a manual check.

To install an update on the host:

```sh
docker compose pull && docker compose up -d
```

For unattended updates, pin a tag (`image: ghcr.io/miramedia-app/miramedia:v0.2.0`)
and bump it deliberately, or track `:latest` and run the host command above on a
schedule. The **System → Updates** page detects new releases but does not apply
them from inside the container.

## Architecture

### Backend Modules

| Module | Description | API Prefix |
|---|---|---|
| `miramedia/shows/` | TV show management (shows, seasons, episodes) | `/api/v1/shows/` |
| `miramedia/movies/` | Movie management | `/api/v1/movies/` |
| `miramedia/torrents/` | Torrent lifecycle, unified download system, manual add, scoring | `/api/v1/torrents/` |
| `miramedia/indexers/` | Torrent search (native sites, Prowlarr, Jackett, custom Torznab) | `/api/v1/indexers/` |
| `miramedia/imports/` | Import pipeline, recovery dashboard, library scan | `/api/v1/torrents/` |
| `miramedia/metadata/` | Metadata providers (native TVmaze+Cinemeta, TMDB, TVDB) | - |
| `miramedia/notifications/` | Notification system | `/api/v1/notifications/` |
| `miramedia/streams/` | Media streaming & subtitle delivery | `/api/v1/streams/` |
| `miramedia/subtitles/` | Subtitle management (subliminal; subdl/subsource/yifysubtitles; vendored keyless plugins; optional Bazarr) | `/api/v1/subtitles/` |
| `miramedia/requests/` | Media request system (native + Seerr composite, optional) | `/api/v1/requests/` |
| `miramedia/cloudflare/` | Shared Cloudflare bypass (nodriver + curl_cffi) | - |
| `miramedia/updates/` | GitHub release checks + optional in-app apply | `/api/v1/system/` |
| `miramedia/logs/` | Database-backed system logs | `/api/v1/system/` |
| `miramedia/settings/` | System configuration | `/api/v1/system/` |
| `miramedia/auth/` | Authentication (JWT, cookies, OAuth/OIDC) | `/api/v1/auth/` |
| `miramedia/database/` | SQLAlchemy + Alembic setup | - |

### Unified Torrent System

All torrent operations go through `TorrentService.download_and_link()`, which handles:
1. Downloading the torrent via the configured download client
2. Pausing the download
3. Creating `EpisodeFile` (TV) or `MovieFile` (movie) records linking the torrent to media
4. Resuming the download

**Unified API endpoints:**
- `GET /api/v1/torrents/search` — search for torrents for any media type
- `POST /api/v1/torrents/download` — download and link a torrent to media
- `POST /api/v1/torrents/manual/parse` — parse a magnet link or .torrent file, fuzzy-match against library
- `POST /api/v1/torrents/manual/download` — download a previously parsed manual torrent

All torrent search and download flows go through these unified endpoints regardless of media type.

### Frontend

`web/` — Next.js 16 (App Router, **static-export SPA**), React 19, Tailwind v4,
shadcn/ui, TanStack Query/Table, react-hook-form + zod. The frontend talks to
the backend over CORS; types are generated from the backend OpenAPI schema into
`web/src/lib/api/api.d.ts`. Key component patterns:
- `download-dialogs/download-media-dialog.tsx` — unified search + download dialog for any media type
- `torrents/add-torrent-dialog.tsx` — manual torrent add (magnet / .torrent upload with media matching)

### Stack

Python 3.13 · FastAPI · SQLAlchemy · Alembic · Pydantic-Settings · taskiq
(scheduler) · PostgreSQL · libtorrent · guessit · subliminal (+ subdl/subsource/yifysubtitles, vendored plugins) · TypeScript ·
Next.js 16 · React 19.

## Developer Quick Start

Fully Dockerized dev environment with hot-reload for backend (FastAPI) and
frontend (Next.js Fast Refresh); PostgreSQL is provisioned automatically.

```sh
mkdir -p res/config                 # first run only
cp config.dev.toml res/config/config.toml
cp web/.env.example web/.env
make up                             # docker compose -f docker-compose.dev.yaml up -d --build
```

- Frontend: http://localhost:5555 · Backend API: http://localhost:8001 · DB: localhost:5433
- Bootstrap admin is auto-created when the user table is empty; credentials are printed in `make logs`.

Common commands:

| Command | What it does |
|---|---|
| `make up` / `make down` | start / stop the dev stack |
| `make logs ARGS="--follow miramedia"` | tail backend logs |
| `make app` / `make frontend` | shell into the backend / frontend container |
| `make check` | fast local gate: lint, format-check, ty, test, frontend typecheck, and migration audit |
| `make check-ci` | pre-PR CI parity: `check` + production frontend build + OpenAPI drift check + PostgreSQL integration tests + Playwright e2e (`MIRAMEDIA_TEST_DATABASE_URL` and Chromium required) |
| `make frontend-e2e` | Playwright browser smoke tests (`cd web && pnpm exec playwright install --with-deps chromium` once) |
| `make lint` / `make format` / `make format-check` / `make ty` | backend lint, format, format check, typecheck |
| `make test` | run the backend test suite on the host |
| `make frontend-bootstrap` | fresh-clone web setup (`pnpm install` + fumadocs-mdx + next typegen) |
| `make openapi` | regenerate `web/src/lib/api/api.d.ts` from the OpenAPI schema |
| `make tsc` | type-check the frontend |

Alembic migrations run automatically on backend startup. Create one with
`make app` then `uv run --python 3.13 alembic revision --autogenerate -m "description"`.

Local (non-Docker) backend setup and the full guide:
[Developer Guide](https://miramedia-app.github.io/miramedia/docs/contributing-to-miramedia/developer-guide/).

<!-- LICENSE -->

## License

Distributed under the AGPL 3.0. See `LICENSE` for more information.

MiraMedia is a derivative work of [MediaManager](https://github.com/maxdorninger/MediaManager) by Maximilian Dorninger, also licensed under the AGPL 3.0.


<!-- ACKNOWLEDGMENTS -->

## Acknowledgments

* MiraMedia is a fork of [MediaManager](https://github.com/maxdorninger/MediaManager), originally created by [Maximilian Dorninger](https://github.com/maxdorninger). Huge thanks to him and the original contributors for the foundational work this project builds on.
* [Thanks to Sándor Bányai for the image on the login screen](https://unsplash.com/photos/a-black-and-white-photo-of-a-film-strip-ER_2eKPscTM)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=miramedia-app/miramedia&type=Date)](https://www.star-history.com/#miramedia-app/miramedia&Date)


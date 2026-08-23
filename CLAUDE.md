# MiraMedia — agent guide

## Commands

| Task | Command |
|---|---|
| Check (fast local gate, host: `uv` + Python 3.13 + `pnpm`; run `make frontend-bootstrap` once per fresh clone) | `make check` |
| Check (CI parity, needs Postgres + Chromium) | `make check-ci` |
| Test | `make test` |
| Lint | `make lint` |
| Format | `make format` |
| Format check | `make format-check` |
| Typecheck (backend) | `make ty` |
| Typecheck (frontend) | `make tsc` |
| Frontend tests | `make frontend-test` (= `cd web && pnpm test`; vitest, Node-only, no backend) |
| Frontend browser smoke | `make frontend-e2e` (= `cd web && pnpm run test:e2e`; install Chromium first) |
| Real frontend smoke | `make frontend-smoke-real` (= `cd web && pnpm run test:smoke`; needs Postgres + static export) |
| Frontend lint + format check | `make frontend-lint` |
| Frontend bootstrap | `make frontend-bootstrap` |
| Frontend generate (non-build paths only) | `make frontend-generate` (= `cd web && pnpm run generate`) |
| OpenAPI regen | `make openapi` (backend must be importable; writes `web/src/lib/api/api.d.ts`) |
| Dev stack up | `make up` (docker-compose.dev.yaml; `make dev` for watch mode) |

CI uses `UV_PYTHON=3.13` for all backend steps (see `.github/workflows/ci.yml`).

## Architecture

FastAPI backend in `miramedia/` — per-domain modules: `shows`, `movies`, `torrents`, `indexers`,
`imports`, `streams`, `subtitles`, `playback`, `watchlists`, `events`, `metadata`, `auth`,
`notifications`, `requests`, `ops`, `logs`, `settings`, `updates`, plus non-routed `core`,
`upcoming`, `observability`, `scheduler_tasks/`, and `background_services.py`. Each routed module
follows a router / service / repository split. API prefix: `/api/v1/{domain}/` (exceptions: `core`
routes at `/api/v1/health`, `/api/v1/features`, etc.; `observability` at `/api/v1/analytics`).

Next.js 16 static-export SPA in `web/` — React 19, Tailwind v4, shadcn/ui.
See `web/CLAUDE.md` (→ `web/AGENTS.md`): read `node_modules/next/dist/docs/` before writing
any Next code — this version has breaking changes from training data.

Persistence: PostgreSQL 17 + SQLAlchemy ORM + Alembic migrations.
Background jobs: taskiq scheduler with taskiq-postgresql broker — registration in
`miramedia/scheduler.py`, task implementations in `miramedia/scheduler_tasks/`, background service
composition in `miramedia/background_services.py`.
Config: TOML-based (`config.toml`), loaded via pydantic-settings (`miramedia/config.py`).

## Hard rules

- Always pass `--python 3.13` to `uv run` (libtorrent cp313 wheel-only).
- New SQLAlchemy models must be imported in `alembic/env.py` for autogenerate to see them.
- Run `make openapi` after any API change that affects request/response shapes.
- Never edit `config.toml` (the live instance config); use `config.example.toml` for examples.
- Commit style: short imperative subject line, no Co-Authored-By trailer.
- Tests must remain DB-free (no live Postgres required to run the suite).

## Gotchas

- **libtorrent wheel**: cp313 only — CI pins `UV_PYTHON=3.13`; newer interpreters break the install.
- **Fresh-clone frontend**: `pnpm install --frozen-lockfile` alone is not enough — pnpm 10 blocks
  postinstall scripts, so `web/.source` (Fumadocs collections) and Next's type declarations are
  absent. Do NOT rely on `next build` to generate them implicitly: `createMDX` kicks off `init()`
  **without awaiting it**, so on a clean tree the write races the compile and fails with
  `Can't resolve 'collections/server'`. Both `web/package.json` scripts therefore generate
  explicitly first and then set `_FUMADOCS_MDX=1` (Fumadocs' own guard sentinel) to suppress that
  un-awaited re-init:
  - `pnpm build` = `fumadocs-mdx && _FUMADOCS_MDX=1 next build` — self-sufficient. `make
    frontend-build` and Docker just call it; never add a generation step around them.
  - `pnpm run generate` = `fumadocs-mdx && _FUMADOCS_MDX=1 next typegen` — for non-build paths
    (`make frontend-generate`, `make tsc`, CI's typecheck-only job).

  Each path runs MDX exactly once. In CI, the `frontend` job runs `pnpm run generate` once and then
  `test` / `lint` / `format:check` / `typecheck:generated` / `build:generated`; `frontend-e2e` runs
  `pnpm run generate` once before Playwright; `frontend-smoke-real` runs the self-contained
  `pnpm build` (which generates internally) before the real smoke suite. Dockerfile and the docs
  workflow keep calling the self-contained `pnpm build` — never prepend a generate step there.
- **ty config**: lives in `pyproject.toml` under `[tool.ty]`. Don't bulk-suppress diagnostics in
  new code — fix real bugs, configure stub noise specifically.
- **YAML folded scalars**: `#` inside a YAML folded/literal block is NOT a comment — it is literal
  text. (Bit us in compose files.)
- **Idle-in-transaction**: never hold a DB session open across slow I/O (fan-out HTTP, SSE streams,
  file responses). Call `release_session_before_external_io(db)` before any blocking external call.

## Testing

- Async in unit tests: either bare `asyncio.run(coro)` (sync test fn) or
  `@pytest.mark.anyio` on an async test fn — both are established; pick per
  file consistency, do not mass-migrate. anyio is pinned in the dev group.
- FastAPI dependency overrides: use the `override_dependency` fixture from
  `tests/conftest.py` (see `tests/test_playback_router.py`), not a hand-rolled
  save/restore of `app.dependency_overrides`.

# MiraMedia — agent guide

## Commands

| Task | Command |
|---|---|
| Test | `MIRAMEDIA_LOG_FILE=/dev/null uv run --python 3.13 pytest` |
| Lint | `uv run --python 3.13 ruff check .` |
| Format | `uv run --python 3.13 ruff format .` |
| Format check | `uv run --python 3.13 ruff format --check .` |
| Typecheck (backend) | `uv run --python 3.13 ty check miramedia` |
| Typecheck (frontend) | `cd web && pnpm exec tsgo --noEmit` |
| OpenAPI regen | `make openapi` (backend must be importable; writes `web/src/lib/api/api.d.ts`) |
| Dev stack up | `make up` (docker-compose.dev.yaml; `make dev` for watch mode) |

CI uses `UV_PYTHON=3.13` for all backend steps (see `.github/workflows/ci.yml`).

## Architecture

FastAPI backend in `miramedia/` — per-domain modules: `shows`, `movies`, `torrents`, `indexers`,
`imports`, `streams`, `subtitles`, `metadata`, `auth`, `notifications`, `requests`, `logs`,
`settings`, `updates`. Each module follows a router / service / repository split.
API prefix: `/api/v1/{domain}/`.

Next.js 16 static-export SPA in `web/` — React 19, Tailwind v4, shadcn/ui.
See `web/CLAUDE.md` (→ `web/AGENTS.md`): read `node_modules/next/dist/docs/` before writing
any Next code — this version has breaking changes from training data.

Persistence: PostgreSQL 17 + SQLAlchemy ORM + Alembic migrations.
Background jobs: taskiq scheduler with taskiq-postgresql broker (`miramedia/scheduler.py`).
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
- **Fresh-clone frontend**: `pnpm install --frozen-lockfile` alone is not enough. pnpm 10 blocks
  postinstall scripts, so run manually before typechecking or building:
  `pnpm exec fumadocs-mdx && pnpm exec next typegen`
- **ty config**: lives in `pyproject.toml` under `[tool.ty]`. Don't bulk-suppress diagnostics in
  new code — fix real bugs, configure stub noise specifically.
- **YAML folded scalars**: `#` inside a YAML folded/literal block is NOT a comment — it is literal
  text. (Bit us in compose files.)
- **Idle-in-transaction**: never hold a DB session open across slow I/O (fan-out HTTP, SSE streams,
  file responses). Call `release_session_before_external_io(db)` before any blocking external call.

Canonical guide: CLAUDE.md — this file mirrors only the must-not-break rules for tools that don't read it.

- Always pass `--python 3.13` to `uv run` (libtorrent cp313 wheel-only).
- Never edit `config.toml` (the live instance config); use `config.example.toml` for examples.
- Commit style: short imperative subject line, no Co-Authored-By or any co-author trailer.
- Release commits (see `81cb7c5`, `83f779a`, `8ab845e`): subject `Release X.Y.Z: <summary>`;
  body of grouped title-case bullet sections (Features / Reliability / Migrations-CI / Tests, etc.,
  only those that apply); mandatory final line `Bump manifests to X.Y.Z (pyproject, uv.lock,
  web/package.json, openapi.json).`; and actually bump the version string to `X.Y.Z` in all four:
  `pyproject.toml`, `uv.lock` (miramedia entry), `web/package.json`, `web/public/openapi.json`.
- Run `make openapi` after any API change that affects request/response shapes.
- New SQLAlchemy models must be imported in `alembic/env.py` for autogenerate to see them.
- Tests must remain DB-free (no live Postgres required to run the suite).
- Keep personal dev CORS origins in ignored `.env` (`MIRAMEDIA_CORS_URLS`), not in the committed
  `docker-compose.dev.yaml`.

Fast local gate: `make check`. Full command table, architecture, and gotchas in CLAUDE.md.

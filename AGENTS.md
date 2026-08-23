Canonical guide: CLAUDE.md — this file mirrors only the must-not-break rules for tools that don't read it.

- Always pass `--python 3.13` to `uv run` (libtorrent cp313 wheel-only).
- Never edit `config.toml` (the live instance config); use `config.example.toml` for examples.
- Commit style: short imperative subject line, no Co-Authored-By or any co-author trailer.
- Run `make openapi` after any API change that affects request/response shapes.
- New SQLAlchemy models must be imported in `alembic/env.py` for autogenerate to see them.
- Tests must remain DB-free (no live Postgres required to run the suite).

Fast local gate: `make check`. Full command table, architecture, and gotchas in CLAUDE.md.

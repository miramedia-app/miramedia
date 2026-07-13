# PostgreSQL integration tests

These tests exercise real SQL against an ephemeral PostgreSQL 17 database.
The default `make test` / CI backend job does **not** collect this directory.

## Requirements

- PostgreSQL 17 (local container or CI service)
- `MIRAMEDIA_TEST_DATABASE_URL` (preferred) or `DATABASE_URL` using
  `postgresql+asyncpg://…`
- Database name must contain `test` or `integration` (or set
  `MIRAMEDIA_INTEGRATION_ALLOW_ANY_DATABASE=1` for an explicit opt-in)

## Run

```bash
# Example ephemeral database
export MIRAMEDIA_TEST_DATABASE_URL='postgresql+asyncpg://test:test@127.0.0.1:5432/miramedia_integration_test'

make integration-test
```

`make integration-test` runs `alembic upgrade head` on first use (via session
fixture) and fails fast when the URL is missing or not PostgreSQL.

## Scope

- Migration/schema smoke and JSONB semantics
- Atomic scan claim/reclaim CAS (Plans 074/078)
- Integrity audit compare-and-set (Plan 079)

Plan 082 (integrity pagination, scalar lookups, audit chunk) is covered here.
Plan 083 OAuth migration characterization runs via `pytest -m postgresql tests/`
(also invoked by `make integration-test`). Head revision is asserted dynamically.

# Plans 072–086 integration status

Branch: `plans-072-086-integration`

Final integration branch for plans **072–086** (excluding `main`).

## Integration spine

| Merge | Parents | Notes |
|-------|---------|-------|
| `77e054f` | Plan 084 + `ca56e778` | `stream-media-integration` (072, 074–082, frontend foundation) |
| `96050bf` | above + `b5b36c3` | `auth-token-and-settings-reload` (073, 083) |
| `bd4c953` | above + `abf7a24` | `advisor086-safe-archive-extraction` (086) |
| `d364211` | above | PG lane wiring (`disposable_database_sync_url`, CI postgresql step) |
| `7a9dc9c` | above | Default `make test` excludes `postgresql` marker (DB-free) |
| *(this merge)* | above + `653ba1f` | `plan-085-frontend-workflow-tests` (Vitest, frontend CI gates) |

## Per-plan status

| Plan | Title (short) | Status | Lane / tip commit | Key evidence | Verification |
|------|---------------|--------|-------------------|--------------|--------------|
| 072 | Torrent title path containment | **DONE** | `b2373f5` via `95171cf` | `miramedia/torrents/utils.py`, `tests/test_torrent_path_containment.py` | `make test` |
| 073 | Stop logging verification tokens | **DONE** | `d753839` | `miramedia/auth/users.py`, `tests/test_auth_logging.py` | `make test` |
| 074 | Restrict scan resolve to cached paths | **DONE** | `59dc648` | `miramedia/imports/scan_resolve.py`, `tests/test_scan_resolve_security.py` | `make test` |
| 075 | Stream file ID parent binding | **DONE** | `d6702ee` | `miramedia/streams/router.py`, `tests/test_stream_file_binding.py` | `make test` |
| 076 | Canonical frontend generation | **DONE** | `ca56e778` | `web/package.json`, `Makefile`, CI `frontend` job | `make frontend-generate`, `make tsc` |
| 077 | Integrity UI superuser gating | **DONE** | `ca56e778` | `integrity-section.tsx`, `web/src/lib/imports.ts` | `pnpm test` (integrity helpers) |
| 078 | Atomic scan claim / reclaim CAS | **DONE** | `3bd15bf`…`0864f72` | `miramedia/imports/repository.py`, `tests/integration/test_scan_*.py` | `make integration-test` |
| 079 | Integrity compare-and-set | **DONE** | `dc8e126` | `tests/integration/test_integrity_cas.py` | `make integration-test` |
| 080 | SQL-filter active torrents | **DONE** | `7538fc1` | `tests/test_scheduler_torrents.py` | `make test` |
| 081 | Offload manual update checks | **DONE** | `3b6c106` | `tests/test_updates_router.py` | `make test` |
| 082 | Integrity pagination & audit chunking | **DONE** | `53bc362` / `ca56e778` | `tests/integration/test_integrity_*.py` | `make integration-test` |
| 083 | Auth settings hot-reload | **DONE** | `b5b36c3` | `miramedia/auth/runtime.py`, migration `f3a4b5c6d7e8` | `make test`; `pytest -m postgresql` |
| 084 | PostgreSQL integration suite | **DONE** | `22c1138`…`77e054f` | `tests/integration/*`, CI `postgres-integration` | `make integration-test` |
| 085 | Frontend workflow tests | **DONE** | `plan-085-frontend-workflow-tests` → `653ba1f` | Vitest (`web/src/lib/*.test.ts`), CI frontend test/lint/format/build | `make frontend-test`, `make frontend-lint`, `make frontend-build` |
| 086 | Safe archive extraction | **DONE** | `abf7a24` | `miramedia/imports/archive_*.py`, `tests/test_archive_*.py` | `make test` |

## Integrity cache invalidation (Plan 077/082/085)

After dismiss/rebaseline, `integrity-section.tsx` calls
`qc.invalidateQueries({ queryKey: qk.imports.integrity() })` — the **prefix**
without offset/limit — so every cached page is marked stale. Covered by
`web/src/lib/query-keys.test.ts` (`exposes a prefix that matches every page key`).

## Conflicts resolved (085 merge)

| File | Resolution |
|------|------------|
| `Makefile` | Kept Plan 084 `integration-test` / `migration-head-audit` **and** Plan 085 `frontend-test` / `frontend-lint` |
| `pyproject.toml` (prior) | `--ignore=tests/integration -m 'not postgresql'` + both markers |

## Verification commands

Ephemeral database only (`127.0.0.1:55432/miramedia_integration_test`):

```bash
uv sync --frozen
make lint && make format-check && make ty
make test                         # 926 passed, 15 postgresql deselected (DB-free)
make frontend-test                # 50 passed (vitest)
make frontend-lint && make tsc && make frontend-build
make openapi && git diff --exit-code web/public/openapi.json web/src/lib/api/api.d.ts
export MIRAMEDIA_TEST_DATABASE_URL='postgresql+asyncpg://test:test@127.0.0.1:55432/miramedia_integration_test'
make integration-test             # 28 integration + 15 postgresql
uv run python scripts/migration_head_audit.py --verify-db
```

## Remaining risk

- Auth/settings **session-level** PostgreSQL integration beyond OAuth migration
  characterization is still deferred.
- `main` was not touched.

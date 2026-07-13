# Plans 072–086 integration status

Branch: `plans-072-086-integration`

This branch integrates implemented plan lanes **072–084** and **086** without
`main` and without Plan **085** (frontend workflow tests).

## Integration spine

| Merge | Parents | Notes |
|-------|---------|-------|
| `77e054f` | Plan 084 + `ca56e778` | `stream-media-integration` (072, 074–082, frontend foundation) |
| `96050bf` | above + `b5b36c3` | `auth-token-and-settings-reload` (073, 083) |
| `bd4c953` | above + `abf7a24` | `advisor086-safe-archive-extraction` (086) |

Post-merge fixes land on top of `bd4c953` (disposable PG URL normalization,
CI/Makefile `postgresql` lane).

## Per-plan status

| Plan | Title (short) | Status | Lane / tip commit | Key evidence | Verification (2026-07-13) |
|------|---------------|--------|-------------------|--------------|---------------------------|
| 072 | Torrent title path containment | **DONE** | `advisor072-contain-torrent-title-paths` → `95171cf` / `b2373f5` | `miramedia/torrents/utils.py`, `tests/test_torrent_path_containment.py` | `make test` |
| 073 | Stop logging verification tokens | **DONE** | `auth-token-and-settings-reload` → `d753839` | `miramedia/auth/users.py`, `tests/test_auth_logging.py` | `make test` |
| 074 | Restrict scan resolve to cached paths | **DONE** | `scan-restrict-atomic-claim` → `4422419` / `59dc648` | `miramedia/imports/scan_resolve.py`, `tests/test_scan_resolve_security.py` | `make test` |
| 075 | Stream file ID parent binding | **DONE** | `stream-media-binding-updates` → `f859a2c` / `d6702ee` | `miramedia/streams/router.py`, `tests/test_stream_file_binding.py` | `make test` |
| 076 | Canonical frontend generation | **DONE** | `plans-076-077-generation-integrity` → `ca56e778` | `web/package.json`, `Makefile`, `.github/workflows/ci.yml` | `make lint`; CI `frontend` job contract |
| 077 | Integrity UI superuser gating | **DONE** | same as 076 | `web/src/components/imports/integrity-section.tsx`, `web/src/lib/imports.ts` | `make test`; frontend typecheck path in CI |
| 078 | Atomic scan claim / reclaim CAS | **DONE** | `scan-restrict-atomic-claim` → `3bd15bf`…`0864f72` | `miramedia/imports/repository.py`, `tests/integration/test_scan_{claim,reclaim}.py` | `make integration-test` (28) |
| 079 | Integrity compare-and-set | **DONE** | `plans-079-080-integrity-torrents` → `862d254` / `dc8e126` | `miramedia/torrents/service.py`, `tests/integration/test_integrity_cas.py` | `make integration-test` |
| 080 | SQL-filter active torrents | **DONE** | same as 079 / `7538fc1` | `miramedia/torrents/repository.py`, `tests/test_scheduler_torrents.py` | `make test` |
| 081 | Offload manual update checks | **DONE** | `stream-media-binding-updates` / `3b6c106` | `miramedia/updates/router.py`, `tests/test_updates_router.py` | `make test` |
| 082 | Integrity pagination & audit chunking | **DONE** | `plan-082-scheduler-bounds` → `ca56e778` / `53bc362` | `miramedia/torrents/integrity.py`, `tests/integration/test_integrity_*.py` | `make integration-test` |
| 083 | Auth settings hot-reload | **DONE** | `auth-token-and-settings-reload` → `b5b36c3` | `miramedia/auth/runtime.py`, `miramedia/settings/service.py`, `tests/test_auth_settings_hot_reload.py`, migration `f3a4b5c6d7e8` | `make test`; `pytest -m postgresql` (15) on ephemeral PG 17 |
| 084 | PostgreSQL integration suite | **DONE** | `plan-084-integration-suite` → `22c1138`…`77e054f` | `tests/integration/*`, `tests/integration/db_ready.py`, CI `postgres-integration` | `make integration-test` (43 PG tests total) |
| 085 | Frontend workflow tests | **OUT OF SCOPE** | `plan-085-frontend-workflow-tests` | not merged | — |
| 086 | Safe archive extraction | **DONE** | `advisor086-safe-archive-extraction` → `abf7a24` | `miramedia/imports/archive_*.py`, `tests/test_archive_*.py` | `make test` (archive suite) |

## Conflicts resolved

| File | Resolution |
|------|------------|
| `pyproject.toml` | Kept Plan 084 `--ignore=tests/integration` **and** auth `postgresql` marker |
| (others) | Auto-merged cleanly |

## Verification commands

Ephemeral database only (`127.0.0.1:55432/miramedia_integration_test` — never
`127.0.0.1:5433/miramedia`):

```bash
export MIRAMEDIA_TEST_DATABASE_URL='postgresql+asyncpg://test:test@127.0.0.1:55432/miramedia_integration_test'
uv sync --frozen
make lint && make format-check && make ty
make test                    # 926 passed, 15 postgresql deselected (DB-free)
make integration-test        # 28 integration + 15 postgresql
uv run python scripts/migration_head_audit.py --verify-db  # head f3a4b5c6d7e8
```

## Remaining risk

- Dedicated auth/settings **session-level** PostgreSQL integration cases (beyond
  OAuth migration `postgresql` tests) are not in scope for this merge.
- Plan **085** remains on its own branch.
- `main` was not touched.

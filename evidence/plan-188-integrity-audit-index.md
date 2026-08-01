# Plan 188: Integrity-audit chunk query index spike

**Date**: 2026-07-30 (revised after review)  
**Decision**: NO CHANGE — audit chunk query is PK index-served; dedicated partial index on `import_status = 'imported'` not warranted.

## Revision note

The initial seed placed all 500 `pending` rows above the maximum `imported` UUID, so `id <= ep_max_id` excluded every pending row and the `import_status` filter was never exercised. This revision reseeds with **random UUID v4 primary keys** and **pending rows evenly interleaved in UUID sort order**, with all 500 pending rows inside the audit scan range.

## Drift check

```
git diff --stat 3b57c20..HEAD -- miramedia/shows/models.py miramedia/movies/models.py miramedia/scheduler.py alembic/
```

(no diff — models and scheduler unchanged since plan baseline)

## Database

- **Instance**: `docker-compose.dev.yaml` Postgres 17 on `localhost:5433`
- **Credentials**: `miramedia` / `miramedia` / `miramedia`
- **Schema**: `alembic upgrade head`

### Seed methodology (corrected)

1. Generate 5,500 random `uuid4()` values; sort ascending (simulates UUID v4 btree order in production).
2. Mark 500 evenly spaced positions in that sorted list as `pending`; remaining 5,000 are `imported`.
3. Insert identical rows into `episode_file` and `movie_file` (same `id` / `import_status` per row).
4. `ANALYZE` both tables.

### Exact distribution (verified via SQL)

| Metric | Value |
|--------|-------|
| Total rows | 5,500 |
| `imported` | 5,000 (90.9%) |
| `pending` | 500 (9.1%) |
| `ep_max_id` / `mv_max_id` | `fffe59ad-2709-4204-a9be-2084063d1604` |
| `pending` with `id <= ep_max_id` | **500 / 500** (all pending inside scan range) |
| `pending` above `ep_max_id` | 0 |
| Global max `id` status | `imported` |

Pending rows are spread across the full UUID/id range; each 100-row result chunk requires the planner to skip ~10 pending rows on average (`Rows Removed by Filter: 10`).

## Step 1 — Live index set

### `episode_file`

```
Indexes:
    "episode_file_pkey" PRIMARY KEY, btree (id)
    "ix_episode_file_episode_id" btree (episode_id)
    "ix_episode_file_import_status" btree (import_status)
    "ix_episode_file_import_status_pending" btree (import_status) WHERE import_status <> 'imported'
    "ix_episode_file_sha1_pending" btree (episode_id) WHERE sha1 IS NULL AND import_status = 'imported'
    "ix_episode_file_torrent_id" btree (torrent_id)
    "uq_episode_file_naming" UNIQUE CONSTRAINT, btree (episode_id, quality, codec, variant, extra)
```

### `movie_file`

```
Indexes:
    "movie_file_pkey" PRIMARY KEY, btree (id)
    "ix_movie_file_import_status" btree (import_status)
    "ix_movie_file_import_status_pending" btree (import_status) WHERE import_status <> 'imported'
    "ix_movie_file_movie_id" btree (movie_id)
    "ix_movie_file_sha1_pending" btree (movie_id) WHERE sha1 IS NULL AND import_status = 'imported'
    "ix_movie_file_torrent_id" btree (torrent_id)
    "uq_movie_file_naming" UNIQUE CONSTRAINT, btree (movie_id, quality, codec, variant, extra)
```

Model declarations (`index=True` on FK/status columns; partial index on non-imported rows) match the live schema.

## Step 2 — EXPLAIN ANALYZE (audit chunk query)

Query shape (from `miramedia/scheduler.py`):

```sql
SELECT * FROM episode_file
WHERE import_status = 'imported'
  AND id > :last_id
  AND id <= :ep_max_id
ORDER BY id
LIMIT 100;
```

Shared parameters: `ep_max_id = fffe59ad-2709-4204-a9be-2084063d1604`, `chunk_limit = 100`.

Chunk cursors:

| Chunk | `last_id` |
|-------|-----------|
| First | `00000000-0000-0000-0000-000000000000` |
| Mid | `842c57fc-434e-475d-ba1b-94dc0f3cfee3` (imported row at ~50th percentile) |
| Near boundary | `f80970fa-b405-47cc-a404-02a24d5ebf46` (imported row ~150 from end) |

### Episode — first chunk

```
 Limit  (cost=0.28..9.21 rows=100 width=149) (actual time=0.017..0.038 rows=100 loops=1)
   Buffers: shared hit=4
   ->  Index Scan using episode_file_pkey on episode_file  (cost=0.28..446.73 rows=5000 width=149) (actual time=0.016..0.030 rows=100 loops=1)
         Index Cond: ((id > '00000000-0000-0000-0000-000000000000'::uuid) AND (id <= 'fffe59ad-2709-4204-a9be-2084063d1604'::uuid))
         Filter: (import_status = 'imported'::importoutcome)
         Rows Removed by Filter: 10
         Buffers: shared hit=4
 Execution Time: 0.091 ms
```

### Episode — mid chunk

```
 Limit  (cost=0.28..9.24 rows=100 width=149) (actual time=0.013..0.047 rows=100 loops=1)
   Buffers: shared hit=6
   ->  Index Scan using episode_file_pkey on episode_file  (cost=0.28..221.95 rows=2475 width=149) (actual time=0.012..0.039 rows=100 loops=1)
         Index Cond: ((id > '842c57fc-434e-475d-ba1b-94dc0f3cfee3'::uuid) AND (id <= 'fffe59ad-2709-4204-a9be-2084063d1604'::uuid))
         Filter: (import_status = 'imported'::importoutcome)
         Rows Removed by Filter: 10
         Buffers: shared hit=6
 Execution Time: 0.079 ms
```

### Episode — near boundary

```
 Limit  (cost=0.28..10.19 rows=100 width=149) (actual time=0.015..0.031 rows=100 loops=1)
   Buffers: shared hit=4
   ->  Index Scan using episode_file_pkey on episode_file  (cost=0.28..12.66 rows=125 width=149) (actual time=0.014..0.024 rows=100 loops=1)
         Index Cond: ((id > 'f80970fa-b405-47cc-a404-02a24d5ebf46'::uuid) AND (id <= 'fffe59ad-2709-4204-a9be-2084063d1604'::uuid))
         Filter: (import_status = 'imported'::importoutcome)
         Rows Removed by Filter: 10
         Buffers: shared hit=4
 Execution Time: 0.071 ms
```

### Movie — first chunk

```
 Limit  (cost=0.28..9.21 rows=100 width=149) (actual time=0.014..0.032 rows=100 loops=1)
   Buffers: shared hit=4
   ->  Index Scan using movie_file_pkey on movie_file  (cost=0.28..446.73 rows=5000 width=149) (actual time=0.013..0.025 rows=100 loops=1)
         Index Cond: ((id > '00000000-0000-0000-0000-000000000000'::uuid) AND (id <= 'fffe59ad-2709-4204-a9be-2084063d1604'::uuid))
         Filter: (import_status = 'imported'::importoutcome)
         Rows Removed by Filter: 10
         Buffers: shared hit=4
 Execution Time: 0.079 ms
```

### Movie — mid chunk

```
 Limit  (cost=0.28..9.24 rows=100 width=149) (actual time=0.032..0.096 rows=100 loops=1)
   Buffers: shared hit=6
   ->  Index Scan using movie_file_pkey on movie_file  (cost=0.28..221.95 rows=2475 width=149) (actual time=0.031..0.050 rows=100 loops=1)
         Index Cond: ((id > '842c57fc-434e-475d-ba1b-94dc0f3cfee3'::uuid) AND (id <= 'fffe59ad-2709-4204-a9be-2084063d1604'::uuid))
         Filter: (import_status = 'imported'::importoutcome)
         Rows Removed by Filter: 10
         Buffers: shared hit=6
 Execution Time: 0.151 ms
```

### Movie — near boundary

```
 Limit  (cost=0.28..10.19 rows=100 width=149) (actual time=0.011..0.028 rows=100 loops=1)
   Buffers: shared hit=5
   ->  Index Scan using movie_file_pkey on movie_file  (cost=0.28..12.66 rows=125 width=149) (actual time=0.009..0.021 rows=100 loops=1)
         Index Cond: ((id > 'f80970fa-b405-47cc-a404-02a24d5ebf46'::uuid) AND (id <= 'fffe59ad-2709-4204-a9be-2084063d1604'::uuid))
         Filter: (import_status = 'imported'::importoutcome)
         Rows Removed by Filter: 10
         Buffers: shared hit=5
 Execution Time: 0.063 ms
```

**Summary**: All six plans use **Index Scan on PK** with an `import_status` filter. No sequential scan. With ~9% pending interleaved across the range, each 100-row chunk removes **10 pending rows** via the filter (~110 index entries examined per chunk). Execution times 0.06–0.15 ms.

## Step 3 — Decision

**NO MIGRATION.**

Rationale:

1. PERF-07 premise rejected — FK and `import_status` indexes already exist (confirmed via `\d`).
2. With interleaved pending rows inside the scan range, the audit chunk query still uses `episode_file_pkey` / `movie_file_pkey`; the planner applies `import_status = 'imported'` as a cheap per-row filter (`Rows Removed by Filter: 10` per 100-row chunk at ~9% pending rate).
3. A partial index `(id) WHERE import_status = 'imported'` would only help if a **large fraction** of the id range were non-imported (plan threshold: library dominated by pending/failed). At ~9% pending with interleaved UUIDs, filter overhead is negligible; a dedicated index is not justified.
4. No seq scan or unexpected plan nodes observed.

## For `plans/README.md` (orchestrator)

> **PERF-07 rejected**: FK/status indexes already exist on `episode_file` and `movie_file`; nightly integrity-audit chunk query is PK index-served with cheap `import_status` filter even when ~9% pending rows are interleaved in UUID order (EXPLAIN ANALYZE, 5.5k-row seeded dev DB, 2026-07-30). No partial `import_status = 'imported'` index added.

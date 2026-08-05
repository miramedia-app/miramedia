# Design 238: Durable per-user playback progress

> Status: **design only** — no migrations, endpoints, or UI shipped.
> Planned against commit `96bc35f`. Branch: `238-playback-progress-design`.
>
> Evidence: `evidence/238-playback-write-cadence.md`

## Verdict

Use a **per-user / per-media-file** progress row (dual FK to `movie_file` /
`episode_file`), a dedicated `/api/v1/playback/` module owned by
`current_active_user`, and a **15 s debounced** client write cadence capped at
**240 steady-state writes/hour/active player**. Ship in three independently
testable slices: persistence API → player sync → resume / Continue Watching UX.

---

## 1. Identity, media-file, and player lifecycle map

### 1.1 Live identity chain

| Layer | Live symbol | Identity carried |
|---|---|---|
| Auth | `current_active_user` in `miramedia/auth/users.py` (`fastapi_users.current_user(active=True, verified=True)`) | `User.id` (`UUID`, `miramedia/auth/db.py::User`) |
| Stream auth gate | `miramedia/streams/router.py` `APIRouter(prefix="/streams", …, dependencies=[Depends(current_active_user)])` | Any authenticated active verified user may stream; **not** file-owner scoped |
| Movie file | `miramedia/movies/models.py::MovieFile.id` (surrogate UUID PK) | Stable playable-file id; FK `movie_id` → `movie.id` `ON DELETE CASCADE` |
| Episode file | `miramedia/shows/models.py::EpisodeFile.id` (surrogate UUID PK) | Stable playable-file id; FK `episode_id` → `episode.id` `ON DELETE CASCADE` |
| Player props | `VideoPlayerDialog({ mediaType, mediaId, fileId, title, ... })` in `web/src/components/video-player-dialog.tsx` | `fileId` = file UUID; `mediaId` = `movie.id` or `episode.id`; `mediaType` `"movie" \| "show"` |
| Stream URL | dialog builds ``/api/v1/streams/{movies\|episodes}/{mediaId}?file_id={fileId}`` | Server resolves via `get_movie_file_by_id` / `get_episode_file_by_id` in `miramedia/streams/router.py` |
| Call sites | Movies: `web/src/app/(dashboard)/dashboard/movies/[movieId]/client-page.tsx` (`fileId={r.data.id!}`, `mediaId={movie.id}`); Shows: `web/src/components/shows/show-tree-section.tsx` (`fileId={r.data.id!}`, `mediaId={r.episodeId}`, `mediaType="show"`) | One Play control **per imported file row** |

**Drift vs plan text:** there is no `web/src/components/player/` tree. Lifecycle
ownership is `VideoPlayerDialog` + optional `StreamingPlayer`
(`web/src/lib/mediabunny.ts`). Docs: `web/content/docs/user-guide/playing-media.mdx`.

`miramedia/media_state.py` tracks **library download completeness**
(`ProgressStatus` for wanted/downloaded counts), **not** user watch position.
Do not overload it.

### 1.2 Multi-file titles

One logical title maps to **many** playable files:

- `Movie.movie_files` / `Episode.episode_files` — unique on naming tuple
  `(quality, codec, variant, extra)` plus parent id
  (`uq_movie_file_naming` / `uq_episode_file_naming`).
- UI already mounts a separate `VideoPlayerDialog` per imported file row.

Therefore progress **must** key on **file id**, not only `movie_id` /
`episode_id`. Continue Watching may *display* the parent title but must deep-link
back to the same `fileId` (or explicitly choose another file — out of v1 scope).

### 1.3 Player lifecycle (ordered event flow)

Today the dialog does **not** observe playback position for persistence. Native
`<video controls autoPlay>` is the position source of truth for all paths that
set `videoSrc`; `StreamingPlayer.attach` also drives the same element via MSE.

```mermaid
sequenceDiagram
  participant User
  participant Dialog as VideoPlayerDialog
  participant Video as HTMLVideoElement
  participant MB as StreamingPlayer (optional)
  participant API as /api/v1/streams + future /playback

  User->>Dialog: DialogTrigger / onOpenChange(true)
  Dialog->>Dialog: handleOpen(true) → loadAndPlay()
  Dialog->>API: GET .../probe?file_id= (credentials)
  alt direct / HLS native / blob src
    Dialog->>Video: set videoSrc + playerState=playing
  else needs MSE remux
    Dialog->>MB: new StreamingPlayer(source, duration)
    MB->>Video: attach(video, startTime=0); seeking→buffer refill
  end
  Note over Video: Native events available but unwired today:<br/>timeupdate (~4Hz), pause, play, seeking/seeked, ended, error
  User->>Dialog: seek / pause / close / navigate away
  Dialog->>Dialog: handleOpen(false) → cleanup() (dispose MB, revoke blobs)
  Dialog-->>Video: unmount / src cleared
```

| Browser / dialog event | Current handler | Progress write role (proposed) |
|---|---|---|
| `timeupdate` | none | Debounced PUT (15 s) while playing |
| `pause` | none | Immediate flush PUT |
| `ended` | none | Flush; mark completed |
| `seeking` / `seeked` | only in `StreamingPlayer` buffer path (`mediabunny.ts`) | No extra write; next debounce/flush carries new `currentTime` |
| Dialog close (`onOpenChange(false)`) | `cleanup()` | Flush before dispose (await with short timeout) |
| React unmount | `useEffect(() => () => cleanup(), …)` | Same flush best-effort |
| `visibilitychange` → hidden / `pagehide` | none | Best-effort flush (`fetch`+`keepalive` or `sendBeacon`); **not** sole path |
| `error` → `fallbackToMediabunny` | dialog `onError` | No progress write; keep last good position |
| Mediabunny `dispose()` | cleanup | After flush |

**Stable identity at the player boundary:** yes — `fileId: string` (UUID) is
required props and already encoded into every stream/subtitle URL. STOP
condition “no stable media-file identity” does **not** apply.

**UI → API kind map:** player `mediaType: "movie" | "show"` maps to API
`media_kind: "movie" | "episode"` (episode file keyed by `fileId`; parent
`mediaId` remains `episode.id`).

---

## 2. Persistence and privacy contract

### 2.1 Alternatives compared

| Option | Keys | Pros | Cons |
|---|---|---|---|
| **A. Per-user / per-media-file (selected)** | `(user_id, movie_file_id)` xor `(user_id, episode_file_id)` | Matches player `fileId`; correct duration per file; CASCADE with file delete; multi-quality safe | Continue Watching needs join to parent title; dual FK shape |
| B. Per-user / logical media | `(user_id, movie_id)` or `(user_id, episode_id)` | Simpler Continue Watching join | Ambiguous when multiple files exist; duration/position nonsense after quality upgrade; resume may open wrong file |
| C. Append-only watch events / analytics | event stream | Rich history | Out of scope; privacy + write amplification; plan STOP if broader analytics required |
| D. Client-only (`localStorage`) | browser profile | Zero server writes | No cross-device; lost on clear; contradicts “durable” goal |

### 2.2 Selected model (Option A)

New table `playback_progress` (name bikeshed-ok; module `miramedia/playback/`):

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | Surrogate |
| `user_id` | `UUID` FK → `user.id` **ON DELETE CASCADE** | Owner; pattern mirrors `UserApiToken.user_id` |
| `movie_file_id` | `UUID` NULL FK → `movie_file.id` **ON DELETE CASCADE** | Exactly one of movie/episode file set |
| `episode_file_id` | `UUID` NULL FK → `episode_file.id` **ON DELETE CASCADE** | |
| `position_ms` | `Integer` ≥ 0 | Integer milliseconds (avoid float drift) |
| `duration_ms` | `Integer` > 0 | Last observed media duration from player |
| `completed` | `Boolean` | Derived on write; see threshold |
| `updated_at` | `timestamptz` | Server clock on each successful upsert |

Constraints:

- `CHECK` exactly one of `movie_file_id` / `episode_file_id` is non-null
  (same style as `media_request_type_matches_fk` in `miramedia/requests/models.py`).
- Partial unique indexes: `(user_id, movie_file_id) WHERE movie_file_id IS NOT NULL`
  and `(user_id, episode_file_id) WHERE episode_file_id IS NOT NULL`.
- Index `(user_id, updated_at DESC)` for Continue Watching.

Optional denormalized `movie_id` / `episode_id` / `show_id` **not** required for
v1 if list queries join `movie_file → movie` / `episode_file → episode → season → show`.
Add later only if list latency demands it.

### 2.3 Units, completion, timestamps

- **Units:** `position_ms`, `duration_ms` (client sends seconds × 1000, rounded).
- **Completion threshold:** `completed = position_ms >= max(duration_ms - 30_000, floor(0.90 * duration_ms))`.
  Seeking backward clears `completed` when below threshold.
- **Noise floor:** ignore upserts with `position_ms < 5_000` unless `completed` or
  explicit reset — avoids “opened and closed” clutter.
- **`updated_at`:** always server-side `now()`; clients do not supply clocks for
  conflict winners (avoids skewed device clocks).

### 2.4 Privacy, retention, deletion

| Event | Behavior |
|---|---|
| User deleted | CASCADE removes all their progress rows |
| Movie/episode file deleted | CASCADE removes progress for that file only |
| Parent movie/show/episode deleted | File rows cascade → progress cascades |
| User opt-out / reset one title | `DELETE` progress row (owner only) |
| User reset all | `DELETE FROM playback_progress WHERE user_id = :me` |
| Retention TTL | **None in v1** — operational resume data, not analytics. Revisit only with an explicit product retention setting; do not silently expire mid-series resumes |

**Authorization:** every read/write/list filters `user_id == current_active_user.id`.
No admin cross-user read in v1. Streams adjacency is **not** ownership of this
API — progress lives in `miramedia/playback/`, not under `streams/`.

**Not collected:** watch histograms, bitrate, device fingerprints, other users’
activity, recommendations derived from peers.

### 2.5 Concurrency (tabs / devices)

**Policy: last-write-wins on server `updated_at`.**

- Each PUT fully replaces `position_ms` / `duration_ms` / `completed` for that
  `(user, file)` row (upsert).
- Seeking backward is allowed (user intent); no monotonic position constraint.
- Concurrent tabs: whichever PUT commits last wins; acceptable for resume.
- Optional v1.1: `If-Unmodified-Since` / `updated_at` precondition → `409` for
  strict sync; **not** required for MVP.

### 2.6 Migration / rollback

- Forward: Alembic revision creating `playback_progress` + FKs + partial uniques;
  import model in `alembic/env.py` (hard rule).
- Rollback: `DROP TABLE playback_progress` — no backfill; feature is additive.
- No change to `movie_file` / `episode_file` / `user` schemas beyond FK targets.
- Integration tests (Postgres) mandatory when implementing; unit suite stays
  DB-free via mocked repository (repo hard rule).

### 2.7 Rejected options (summary)

- **Logical-media key:** multi-file ambiguity and duration mismatch.
- **Analytics event log:** plan out-of-scope / STOP.
- **Streams-owned endpoints:** auth adjacency only; keeps download path free of
  progress side effects.
- **Client-only storage:** not durable across devices.

---

## 3. Bounded API and write cadence

### 3.1 API contract (`/api/v1/playback`)

Router: `miramedia/playback/router.py`, `dependencies=[Depends(current_active_user)]`,
mounted from `miramedia/main.py` like other domain routers (e.g. streams).

| Method | Path | Body / query | Behavior |
|---|---|---|---|
| `GET` | `/progress` | `file_id: UUID` (+ optional `media_kind`) | Owner row or `200` + `null` body (prefer over bare `404` for simpler UI) |
| `PUT` | `/progress` | `{ file_id, media_kind: "movie"\|"episode", position_ms, duration_ms }` | Upsert owner row; validate bounds; set `completed`; idempotent replace |
| `DELETE` | `/progress` | `file_id` | Reset one file |
| `DELETE` | `/progress/all` | — | Reset all for current user (opt-out / privacy) |
| `GET` | `/continue` | `limit` (default 20, max 50) | Incomplete rows for user, `updated_at DESC`, join title/poster metadata |

Validation bounds:

- `0 ≤ position_ms ≤ duration_ms + 1_000` (1 s slack)
- `1_000 ≤ duration_ms ≤ 86_400_000` (1 s … 24 h)
- `file_id` must exist and match `media_kind`; else `404`
- Always scope by `user_id` on read/write; missing file → `404` (no cross-user
  existence oracle beyond what stream auth already allows)

Idempotency: PUT is naturally idempotent for identical payloads; no separate
idempotency key.

### 3.2 Write cadence (client)

Primary path: extract `usePlaybackProgress({ fileId, mediaKind, videoRef, open })`
used by `VideoPlayerDialog`.

1. On open: `GET /progress`; if resumable (`!completed && position_ms ≥ 5_000`),
   either auto-seek after `loadedmetadata` when prior position ≥ 5% and not
   completed, or show a small “Resume from m:ss” control (slice 3 decides UX).
2. While playing: coalesce `timeupdate` → at most one PUT per **15 s**.
3. Flush immediately on `pause`, `ended`, dialog close, tab hide / `pagehide`.
4. Do **not** depend solely on unload delivery.
5. Failures: soft — log / toast once; keep trying next flush; offline queue of
   **one** latest position per file in memory (drop on success).

Server soft guard (optional, recommended): ignore or no-op PUT if same user+file
wrote < 5 s ago **and** `|Δposition_ms| < 2_000` (defense in depth; client
still owns the 15 s UX bound).

### 3.3 Estimated max steady-state writes

From `evidence/238-playback-write-cadence.md`:

| Source | Rate |
|---|---|
| Raw `timeupdate` | ~14 400 / hour / active player |
| **Debounced 15 s (selected)** | **≤ 240 / hour / active player** |
| Lifecycle flushes | Sparse; coalesce with pending debounce (do not inflate ceiling) |

**Declared ceiling for reviewers:** **240 writes/hour/active player** steady-state.

### 3.4 How tests prove the bound

1. **Pure helper unit** (`web/src/hooks/use-playback-progress.test.ts` or
   `playback-progress-cadence.test.ts`): fake clock + 250 ms ticks for 60 s →
   assert `putCount ≤ 4`; pause adds ≤ 1 extra.
2. **Dialog/hook integration (vitest):** mock `fetch`/apiClient; fire synthetic
   video events; assert call count ≤ bound for a scripted session.
3. **Backend:** owner isolation, seek-back, concurrent last-write-wins,
   completion threshold, delete cascades (integration), and optional <5 s
   coalesce guard unit test.

---

## 4. Smallest UX and implementation slices

### 4.1 UX (minimal)

- **Resume:** if stored progress is resumable, seek on play start **or** offer an
  accessible “Resume from m:ss” / “Start over” choice (prefer explicit choice for
  a11y — button group, not toast-only). Threshold: show resume when
  `position_ms ≥ max(5_000, 0.05 * duration_ms)` and not `completed`.
- **Completed:** treat as watched; omit from Continue Watching; next open starts
  at 0 (no resume chrome).
- **Continue Watching:** one horizontal row on
  `web/src/app/(dashboard)/dashboard/dashboard-home.tsx` above recommended
  carousels — poster, title, progress bar, link to movie/show detail that opens
  or highlights the file. Empty state: hide section (no empty card farm).
- **Opt-out / reset:** “Clear watch progress” on account settings
  (`web/src/app/(dashboard)/dashboard/account/settings/page.tsx` /
  `web/src/components/account/user-settings.tsx`) calling `DELETE /progress/all`;
  per-title reset from resume UI (“Start over” deletes or zeros row).
- **a11y:** resume controls keyboard-focusable; progress bar
  `role="progressbar"` with `aria-valuenow`; loading/error text for Continue
  Watching fetch failures (non-blocking).

### 4.2 Slice A — Backend persistence + API

| | |
|---|---|
| **Files** | `miramedia/playback/{models,schemas,repository,service,router,__init__}.py`; `alembic/versions/*_playback_progress.py`; `alembic/env.py` import; `miramedia/main.py` mount; `tests/test_playback_*.py`; `make openapi` → `web/src/lib/api/api.d.ts` |
| **Tests** | Owner isolation; upsert/get/delete; completion threshold; seek-back; last-write-wins; validation bounds; file/user delete cascade (integration) |
| **Commands** | `make lint ty test`; `make migration-head-audit`; Postgres integration when available; `make openapi` |
| **Done** | Authenticated CRUD + continue list; no UI; OpenAPI regenerated |
| **Depends** | none |
| **STOP** | Cannot CASCADE to both file tables without breaking migrations; product rejects private owner-only data |

### 4.3 Slice B — Player sync

| | |
|---|---|
| **Files** | `web/src/hooks/use-playback-progress.ts` (+ test); wire
  `web/src/components/video-player-dialog.tsx`; maybe thin API helpers under
  `web/src/lib/` |
| **Tests** | Cadence bound (≤ 240/h ⇒ ≤ 4/min); flush on pause/ended/close; no write below noise floor; error soft-fail |
| **Commands** | `make frontend-test tsc frontend-lint` |
| **Done** | Open dialog + play updates server at bounded rate; close flushes; GET applied to `currentTime` when policy says auto-seek **or** hook exposes position for Slice C chrome |
| **Depends** | Slice A |
| **STOP** | Cannot read `currentTime`/`duration` reliably on a playback path (native + MSE); would require analytics-scale event shipping |

### 4.4 Slice C — Resume UX + Continue Watching

| | |
|---|---|
| **Files** | `video-player-dialog.tsx` resume controls; `dashboard-home.tsx` section;
  optional `web/src/components/continue-watching.tsx`; account clear-all in
  `user-settings.tsx`; short docs note in `playing-media.mdx` |
| **Tests** | Resume / start-over rendering; completed hides resume; continue list empty/loading/error; a11y roles |
| **Commands** | `make frontend-test tsc frontend-lint`; manual smoke against running stack |
| **Done** | User can resume across refresh/device; see Continue Watching; clear all progress |
| **Depends** | Slices A + B |
| **STOP** | Design expands into ratings, cross-user feeds, or full history UI |

### 4.5 Later test matrix (prescribed)

Owner isolation · seek-back · concurrent PUTs · completion threshold · deleted
files/users · bounded update cadence · resume rendering · offline/error recovery
(latest-in-memory retry).

---

## 5. Baseline gates (this design plan)

Run on worktree `238-playback-progress-design` (no production behavior change):

| Gate | Result |
|---|---|
| `make lint ty test migration-head-audit` | Pass (1790 passed; migration-head-audit ok) |
| `make frontend-test tsc frontend-lint` | Pass (162 frontend tests; tsc + oxlint + oxfmt) |
| Design grep seam | Confirmed: no `web/src/components/player/`; seams in `video-player-dialog.tsx` + `mediabunny.ts` |
| `git status` | Design + evidence only — **no** migrations / app code |

---

## 6. STOP conditions re-check

| Condition | Status |
|---|---|
| No stable media-file identity at player | **Clear** — `fileId` UUID prop + stream query |
| Cannot decide private / retained / resettable | **Clear** — owner-only, no TTL, DELETE reset paths |
| Requires broader viewing analytics | **Clear** — single upsert row, not event analytics |
| Spike needs production migration / external service | **Clear** — cadence evidence is arithmetic + doc only |

---

## Appendix: follow-up plan sketch titles

1. Implement playback progress persistence (Slice A)
2. Wire `VideoPlayerDialog` progress sync (Slice B)
3. Resume chrome + Continue Watching (Slice C)

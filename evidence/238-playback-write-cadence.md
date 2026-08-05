# Evidence: playback progress write-cadence bound (plan 238)

Non-production design evidence only. No player instrumentation was left in
application code: `VideoPlayerDialog` currently has **no** `timeupdate` /
`pause` / `ended` listeners (verified 2026-08-03 on branch
`238-playback-progress-design`).

## Observed player seams

| Symbol | Path | Role today |
|---|---|---|
| `VideoPlayerDialog` | `web/src/components/video-player-dialog.tsx` | Dialog + `<video controls>`; cleanup on close / unmount |
| `StreamingPlayer` | `web/src/lib/mediabunny.ts` | MSE remux path; listens to `seeking` (250ms debounce) for buffer refill only |
| Stream URL | same dialog | `/api/v1/streams/{movies\|episodes}/{mediaId}?file_id={fileId}` |

Plan drift note: there is **no** `web/src/components/player/` directory; the live
seam is `video-player-dialog.tsx`.

## Event frequency basis

HTMLMediaElement `timeupdate` while playing is approximately **4 Hz** (~every
250 ms) in Chromium/WebKit (living standard: "about every 250 ms"). That is the
worst-case raw client event rate before any debounce.

```
raw_timeupdate/hour/active_player = 4 * 3600 = 14_400
```

## Proposed client policy (design)

- Debounce progress **PUT** to **15 s** while playing (`timeupdate` coalesced).
- Always flush on `pause`, `ended`, dialog close (`onOpenChange(false)` →
  `cleanup`), `visibilitychange` → hidden, and `pagehide` (best-effort;
  `keepalive`/`sendBeacon` preferred; must not be the only flush path).
- Ignore writes when `duration` is missing/non-finite or position < 5 s
  (noise floor) unless `ended` / completed.

## Steady-state bound

```
max_steady_writes/hour/active_player = 3600 / 15 = 240
```

Lifecycle flushes are sparse (pause/end/close) and are **not** added into the
steady-state ceiling; they replace a pending debounced write rather than
stacking unbounded extras.

| Debounce | Max steady writes/hour/player |
|---|---|
| 10 s | 360 |
| **15 s (selected)** | **240** |
| 30 s | 120 |
| 60 s | 60 |

## How follow-up tests prove the bound

1. Pure unit test of the debounce helper: simulate `timeupdate` every 250 ms for
   60 s → assert ≤ 4 PUTs (15 s cadence) + at most one flush on pause.
2. Component test around `VideoPlayerDialog` (or extracted hook): fake
   `HTMLVideoElement` events; assert network mock call count ≤ bound.
3. Backend optional: rate-limit soft guard (e.g. reject/coalesce < 5 s repeat
   for same `(user, file)`) — defense in depth, not the primary UX cadence.

Reproduction of the arithmetic:

```bash
python3 - <<'PY'
HZ, HOUR = 4.0, 3600
print("raw", HZ * HOUR)
for d in (10, 15, 30, 60):
    print(d, HOUR / d)
PY
```

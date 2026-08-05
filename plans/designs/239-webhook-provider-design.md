# Design 239: Constrained outbound webhook notification provider

> **Status**: design + pure spike only. No production provider, config, UI, or
> docs shipped under this plan. Tip baseline: `96bc35f`.
>
> **Spike helpers** (under `tests/` only — not a production notifications surface):
> `tests/webhook_design_spike/destination_policy.py`,
> `tests/webhook_design_spike/envelope_signing.py`, exercised by
> `tests/test_webhook_*.py`. No `miramedia/notifications/webhook_*.py`.

## Goal

Add a **versioned, signed, destination-constrained** outbound webhook as one more
notification provider, so Home Assistant and similar self-hosted relays can react
to MiraMedia events — without becoming an SSRF / secret-exfiltration / arbitrary-HTTP
feature.

## Non-goals

- Arbitrary URL templating or expression languages
- User-supplied header maps (beyond the fixed signing headers below)
- Incoming webhooks, plugin frameworks, or workflow engines
- Disabling TLS verification
- Unbounded retries, bodies, timeouts, or redirects
- Shipping production config/UI in this design plan

---

## 1. Event inventory and failure semantics

### Delivery path today

```
call site
  → NotificationService.send_notification_to_all_providers(title, message)
      → release_session_before_external_io(db)          # idle-in-TX guard
      → NotificationManager.send_notification(title, message)
          → subject_prefix + throttle/suppress window
          → MessageNotification(title, message)
          → for each configured provider:
                try: provider.send_notification(...)
                except: log.exception (isolated)
      → optional in-app Notification row (native.enabled)
```

Direct callers of `notification_manager.send_notification` skip the in-app DB write
(`scheduler` update notify; TMDB backend errors).

**Failure isolation** (`miramedia/notifications/manager.py:147-157`): each
provider is try/except'd independently. A webhook failure must never block email /
Gotify / ntfy / Pushover or raise out of the manager.

**Throttle** (`manager.py:112-143`): identical `(title, message)` pairs are
suppressed for `MIRAMEDIA_NOTIFY_SUPPRESS_SECONDS` (default 900s). Webhooks inherit
this fan-out; they do not get a separate event stream until a later typed-event
slice (out of scope).

### Call-site inventory (title → message fields)

| Source | Title (literal / pattern) | Message contents | Include in webhook `data` | Exclude / redact |
|---|---|---|---|---|
| `shows/service.py` import success | `TV Show imported successfully` | show name/year, episode summary, **torrent.title** | title + message as-sent | No paths, no magnet/infohash, no credentials |
| `shows/service.py` import failure | `Failed to import TV Show` | show name/year, torrent.title | as-sent | Do not attach stack traces |
| `shows/service.py` missing source | `Source Files Missing` | show name | as-sent | Disk paths omitted today — keep that |
| `shows/service.py` missing episode | `Missing Episode File` | SxxExx, show name | as-sent | — |
| `shows/service.py` autodownload | `Auto-download started` | show, season/episode, **picked.title** | as-sent | — |
| `movies/service.py` downloaded | `Movie Downloaded` | movie name | as-sent | — |
| `movies/service.py` import failed | `Import Failed` | movie name | as-sent | — |
| `movies/service.py` missing / manual | `Source Files Missing` / `Manual Import Required` | movie name | as-sent | — |
| `movies/service.py` autodownload | `Auto-download started` | movie name/year, **picked.title** | as-sent | — |
| `scheduler.py` Seerr/add failure | `Could not add {kind}` | `external_id: {exc}` | as-sent | Exception text only as already constructed; never raw request bodies |
| `scheduler.py` update available | `MiraMedia update available` | versions + **release_url** | as-sent | Public GitHub URL only |
| `metadata/backends/tmdb.py` | `TMDB API Error` | operation + id/query + **`Error: {e}`** | as-sent | Exception strings may leak upstream detail; webhook must not amplify beyond current notify text |

**Stable common envelope**: only `title` and `message` strings that already enter
`MessageNotification` — **not** ORM models, file paths, API keys, or request
headers. Future typed events (`type` other than `notification.message`) are allowed
by the versioned envelope but are a separate implementation plan.

**Disclosure policy**: titles/messages are operator-facing library metadata
(show/movie/torrent names). They may be sensitive in some deployments; the design
treats the configured webhook destination as an **admin trust boundary** (same as
Gotify/ntfy). No additional field-level redaction beyond what is already absent
from the notification text. Logs must never print `signing_secret` or response
bodies that might echo secrets.

---

## 2. Destination policy (threat model)

Home Assistant on a LAN (`http://homeassistant.local:8123/...` or
`https://192.168.x.x/...`) is a **first-class use case**, reconciled via an
**explicit opt-in** — never by default-open private access.

### Config knobs (future `[notifications.webhook]`)

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Provider absent from fan-out when false |
| `url` | `""` | Single destination URL |
| `allow_private_network` | `false` | Opt-in for RFC1918 / loopback / CGNAT + `http` |
| `signing_secret` | `""` | Optional HMAC secret; empty → unsigned delivery |

Timeouts, redirect limits, retry bounds, TLS verify, and User-Agent are **fixed
constants** (not user-tunable) to prevent weakening the policy from the UI.

### Machine-testable acceptance rules (D1–D11)

Implemented in pure `tests/webhook_design_spike/destination_policy.py` /
`tests/test_webhook_destination_policy.py`:

| ID | Rule |
|---|---|
| D1 | Default schemes: **HTTPS only**. Other schemes → deny `scheme`. |
| D2 | Missing hostname → deny `host`. |
| D3 | Userinfo (`user:pass@`) → deny `userinfo` (credentials belong in signing secret, not URL). |
| D4 | Default: resolved RFC1918 (and CGNAT `100.64.0.0/10`) → deny `private`. |
| D5 | Default: loopback literal/resolution → deny `loopback`. |
| D6 | **Link-local always denied** (incl. `169.254.169.254`, `fe80::/10`) even with opt-in. |
| D7 | `allow_private_network=true`: RFC1918 + loopback allowed. |
| D8 | Opt-in also allows **`http`** (typical HA). |
| D9 | Default still denies `http`. |
| D10 | Every URL in a redirect chain must pass the same policy (no hop to link-local/metadata). |
| D11 | Max **3** redirects (`initial + 3`); longer chains → deny `redirect_limit`. |

Additional address matrix: global IPs allowed; multicast/unspecified denied;
IPv4-mapped IPv6 classified via mapped address.

### Abuse cases (no exploit payloads)

| Abuse | Mitigation |
|---|---|
| Blind SSRF to cloud metadata (`169.254.169.254`) | Link-local always denied; redirects re-validated |
| Scan RFC1918 / CGNAT without consent | Default deny; requires `allow_private_network` |
| DNS rebinding (public A → private A between check and connect) | Delivery slice: resolve → validate → **pin** IP for that attempt (same pattern as `miramedia/torrents/utils.py` `_dns_pin`); re-resolve+validate each redirect hop |
| Credential-in-URL exfil via logs | Deny userinfo; redact secrets in settings/export; never log Authorization/signing headers |
| Open redirect bounce to attacker | Redirects limited + each Location re-validated under the same policy |
| Proxy env SSRF (`HTTP_PROXY`) | Delivery client uses `trust_env=False` |
| Huge response / slowloris | Timeout **10s**; read at most **64 KiB** then discard |
| TLS downgrade / MITM | Certificate verification **always on**; no UI toggle |
| Header injection via title | Reuse `sanitize_notification_title` for any header use; body is JSON-encoded |

### Delivery network constants (implementation slices)

| Constant | Value |
|---|---|
| Connect+read timeout | 10 seconds |
| Max redirects | 3 |
| Max response bytes consumed | 65536 |
| TLS verify | required |
| Proxy / `trust_env` | disabled |
| Allowed ports | any (HA `:8123` etc.) once host policy passes |
| Content-Type | `application/json; charset=utf-8` |
| User-Agent | `MiraMedia-Webhook/1` |

**STOP check**: private HA destinations are supported **only** via
`allow_private_network=true`. Unrestricted destinations are never exposed: no slice
may ship `enabled` without URL validation that defaults to public-HTTPS-only.

---

## 3. Envelope, signing, retry

### Versioned JSON envelope (`v1`)

```json
{
  "version": 1,
  "id": "11111111-2222-4333-8444-555555555555",
  "type": "notification.message",
  "time": "2026-08-03T18:00:00.123Z",
  "source": "miramedia",
  "data": {
    "title": "[MiraMedia] Movie Downloaded",
    "message": "Movie Example has been successfully downloaded and imported."
  }
}
```

| Field | Rule |
|---|---|
| `version` | Integer `1` (`ENVELOPE_VERSION`) |
| `id` | UUID4 string; stable across retries of the same send |
| `type` | `notification.message` for current fan-out |
| `time` | UTC ISO-8601 with millisecond precision, `Z` suffix |
| `source` | Constant `miramedia` |
| `data.title` / `data.message` | Exact strings after prefix/suppress handling |

**Canonical serialization** (signing input = POST body):
`json.dumps(..., ensure_ascii=False, separators=(",", ":"))` UTF-8.
Key order fixed by `WebhookEnvelope.to_dict()`.

Pure spike: `tests/webhook_design_spike/envelope_signing.py`.

### Optional HMAC-SHA256 signing

When `signing_secret` is non-empty:

```
material = "{id}.{unix_timestamp}.".encode("utf-8") + body
X-MiraMedia-Webhook-Signature: v1=<hex(hmac_sha256(secret, material))>
X-MiraMedia-Webhook-Id: <id>
X-MiraMedia-Webhook-Timestamp: <unix_timestamp as decimal string>
```

- Primitive: stdlib `hmac` + `hashlib.sha256` (no custom crypto).
- Verify with `hmac.compare_digest`.
- Receivers should reject timestamps outside a skew window (recommend **±5 minutes**);
  MiraMedia does not re-sign on retry with a new timestamp for the same attempt
  batch — one timestamp per logical send, reused on transport retries.
- Empty secret → omit signature headers (unsigned mode for trusted LAN with
  long random HA webhook path).

No arbitrary extra headers. Fixed headers only: `Content-Type`, `User-Agent`, and
the three `X-MiraMedia-Webhook-*` when signed.

### Retry / idempotency

| Rule | Value |
|---|---|
| Max attempts | 3 (initial + 2 retries) |
| Backoff | 1s, 2s (sleep before attempt 2 / 3) |
| Retry on | connect/TLS errors, timeouts, HTTP **408**, **429**, **5xx** |
| Do not retry | **2xx** success; other **4xx**; policy denials (no HTTP) |
| Redirect handling | Manual (`allow_redirects=False`); validate each Location; count toward redirect budget |
| Idempotency | Same envelope `id` + same body + same signature timestamp on retries |
| Non-2xx after budget | Log warning with status code; return `False` like other providers |

---

## 4. Settings, UI, docs, and rollout slices

### Secret masking

`signing_secret` must be treated as a credential. Settings auto-mask today only
fields named in `CREDENTIAL_FIELD_NAMES`
(`miramedia/settings/validation.py:22-31`). Implementation must **add
`signing_secret`** to that frozenset so read/export paths mask it as `********`
via existing `mask_secret_values` / `strip_masked_values` /
`resolve_masked_config` / `carry_forward_secrets`.

Integration test helper must scrub the secret from exception logs
(same pattern as Pushover/`_log_request_exception`).

### Proposed TOML (docs / example only in later slice)

```toml
[notifications.webhook]
enabled = false
url = "https://hooks.example.com/miramedia"
allow_private_network = false
signing_secret = ""
```

Home Assistant example (docs callout, never as insecure default):

```toml
[notifications.webhook]
enabled = true
url = "http://homeassistant.local:8123/api/webhook/REPLACE_WITH_LONG_TOKEN"
allow_private_network = true
signing_secret = "replace-with-long-random-secret"
```

### UI states (later)

- Mirror Gotify card: enable toggle, URL, secret input, Test button.
- Checkbox **“Allow private / loopback destinations (Home Assistant LAN)”** bound to
  `allow_private_network`, with helper text that link-local/metadata IPs remain blocked.
- Test action: run destination policy + one signed probe POST with
  `type=notification.message` title `MiraMedia webhook test` — **after** policy
  validation; never skip policy on Test.
- Disabled when `enabled=false`; saving empty URL with enabled=true fails validation.

### Follow-up slices (independently testable; no unrestricted destination)

#### Slice A — Policy + envelope library (this plan's spike)

| | |
|---|---|
| Files | `tests/webhook_design_spike/*`, `tests/test_webhook_*.py` (promote into `miramedia/notifications/` only in a later implementation plan) |
| Depends on | none |
| Verify | `make lint ty test`; no webhook modules under `miramedia/`; unused by manager |
| Stop | Any production import from `manager.py` / config / router |

#### Slice B — HTTP delivery client (no provider registration)

| | |
|---|---|
| Files | e.g. `miramedia/notifications/webhook_client.py`; tests with `responses`/`httpx` mock + DNS pin fakes |
| Depends on | A |
| Behavior | policy → pin → POST → redirect loop → retry; `trust_env=False`; TLS verify; body cap |
| Verify | unit tests for D-rules at connect time, redirect deny, timeout, non-2xx retry matrix |
| Stop | No `NotificationConfig` field; client not constructed from live config |

#### Slice C — Provider + config (backend only)

| | |
|---|---|
| Files | `notifications/config.py`, `manager.py`, `service_providers/webhook.py`, `config.example.toml`, settings schemas/validation (`signing_secret` in `CREDENTIAL_FIELD_NAMES`), OpenAPI regen |
| Depends on | B |
| Verify | `enabled=false` default; validation rejects private URL unless opt-in; fan-out isolation tests; secret masking tests; `make openapi` |
| Stop | Do not merge if default `allow_private_network` is true or URL validation is skipped when enabled |

#### Slice D — Settings Test endpoint

| | |
|---|---|
| Files | `settings/integration_tests.py`, test schemas, router wiring |
| Depends on | C |
| Verify | Test uses same policy+client; secrets scrubbed in logs; no redirect follow without re-validate |
| Stop | Test must not accept destinations the provider would reject |

#### Slice E — UI

| | |
|---|---|
| Files | `web/.../notifications-tab.tsx`, `test-button.tsx` union, settings defaults |
| Depends on | C+D |
| Verify | frontend-test/lint; private checkbox default unchecked; no free-form header editor |
| Stop | No UI control that disables TLS or raises redirect/timeout limits |

#### Slice F — Docs

| | |
|---|---|
| Files | `web/content/docs/configuration/notifications.mdx`, HA recipe, envelope/signing receiver notes |
| Depends on | C |
| Verify | Documents default-deny private; opt-in for HA; signing headers; no “paste any URL” guidance without warnings |

### Cross-slice test matrix (must remain green)

- Scheme/host/IP/redirect policy (D1–D11 + address matrix)
- DNS-pin / rebinding-safe connect (Slice B+)
- TLS verify on; no insecure mode
- Timeouts + 64 KiB response cap
- Log/settings redaction of `signing_secret`
- Signature determinism + compare_digest verify
- Retry bounds and non-2xx classification
- Disabled config → no HTTP
- Provider exception isolation in manager fan-out

---

## 5. Baseline validation (this plan)

- Pure spike retained under `tests/webhook_design_spike/` only.
- **Not** registered in `_build_providers_uncached`; no `miramedia/notifications/webhook_*.py`.
- No `[notifications.webhook]` in live `config.toml` / example until Slice C.
- No settings UI / docs webhook section until E/F.
- Gates: `make lint ty test` must pass with zero production webhook surface
  (grep: no webhook provider class in `manager.py`; no webhook modules under `miramedia/`).

## Done criteria mapping

| Criterion | Status | Where |
|---|---|---|
| Versioned minimal envelope | met | §3 + `tests/webhook_design_spike/envelope_signing.py` |
| Destination / redirect / TLS / timeout / body / retry explicit | met | §2–§3 |
| Signing secrets ↔ masking/export | met | §4 (`signing_secret` → `CREDENTIAL_FIELD_NAMES`) |
| Safe follow-up slices | met | §4 A–F |
| No production surface in this plan | met | test-only spike + design doc; no provider/config/UI |

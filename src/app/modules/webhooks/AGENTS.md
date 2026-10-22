# Webhooks Module - Agent Guidance

## Package Identity

Tenant-scoped webhook subscriptions and their delivery pipeline: endpoints,
per-endpoint signing secrets, an ARQ-backed delivery queue with an exponential
retry ladder, a per-endpoint circuit breaker and a stuck-delivery recovery cron.

The module is split into three layers that must not be confused:

- `service.py` — endpoint CRUD, cache, triggering, secret rotation. It **delegates**
  every delivery operation to `delivery.py`.
- `delivery.py` — the three delivery lifecycles (`enqueue`, `deliver`,
  `recover_stuck_deliveries`) and the canonical retry ladder.
- `signer.py` — the wire signing contract (`t=<unix>,v1=<hmac>`), owned in one place.

## Setup & Run

```bash
# Unit tests for the module
uv run pytest tests/unit/modules/webhooks/ -v

# Integration tests
uv run pytest tests/integration/modules/webhooks/ -v

# Type check / lint
uv run mypy src/app/modules/webhooks/
uv run ruff check src/app/modules/webhooks/

# Manually run the stuck-delivery recovery job once
uv run python -m src.scripts.recover_webhooks
```

## Endpoints

Router prefix is `/webhooks`; the app mounts it under `/api` + `/v1`
(`src/app/api/__init__.py`, `src/app/api/v1/__init__.py`,
`src/app/api/v1/core.py`), so every path below is `/api/v1/webhooks/...`.

Permissions come from `src/app/core/rbac/permissions.py`:
`Permission.WEBHOOKS_READ` (`webhooks:read`), `Permission.WEBHOOKS_CREATE`
(`webhooks:create`), `Permission.WEBHOOKS_MANAGE` (`webhooks:manage`).

### List endpoints

`GET /api/v1/webhooks` — `Permission.WEBHOOKS_READ`

Query: `is_active` (bool, optional), `event_type` (str, optional), `page`
(int ≥ 1, default 1), `per_page` (int ≤ `MAX_PER_PAGE`, default 100).
Response: `WebhookEndpointList` (a `BaseList[WebhookEndpointRead]`), built by
`build_paginated_response` — `{items, total, page, per_page, has_next, has_previous}`.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/webhooks?is_active=true&per_page=50"
```

### Create endpoint

`POST /api/v1/webhooks` — `Permission.WEBHOOKS_CREATE`, `201 Created`

Request `WebhookEndpointCreate` (`WebhookEndpointBase` + `BaseCreate`):
`url` (HTTPS-only `HttpUrl`), `event_type` (one pattern, e.g. `user.created` or
`user.*`), `is_active` (default true), optional `secret`, optional
`signing_secret`, optional `tenant_id`.

Response `WebhookEndpointCreateResponse`: the endpoint fields **plus**
`secret` and `signing_secret` in plaintext. This is the only read path that
returns the plaintext — the service sets them as transient
`_plaintext_secret` / `_plaintext_signing_secret` attributes on the returned
model instance and the router maps them onto the response.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/hooks","event_type":"user.created"}' \
  http://localhost:8000/api/v1/webhooks
```

### Get / update / delete endpoint

- `GET /api/v1/webhooks/{endpoint_id}` — `WEBHOOKS_READ` → `WebhookEndpointResponse`
  (alias of `WebhookEndpointRead`; it deliberately carries **no** secret).
- `PATCH /api/v1/webhooks/{endpoint_id}` — `WEBHOOKS_MANAGE`, `200 OK` → `WebhookEndpointResponse`.
  Request `WebhookEndpointUpdate` (`BaseUpdate`, `extra="forbid"`): any of `url`,
  `event_type`, `is_active`, `secret`, `signing_secret`. The router sends
  `request.model_dump(exclude_unset=True)`; `WebhookService.update_endpoint`
  applies an allowlist (`url`, `event_type`, `is_active`, `secret`,
  `signing_secret`) and ignores anything else, so a `tenant_id` re-parenting
  can never reach the row. A `null` is rejected for every field except
  `signing_secret` (`ValidationError`).
- `DELETE /api/v1/webhooks/{endpoint_id}` — `WEBHOOKS_MANAGE`, `204 No Content`.

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"is_active":false}' \
  http://localhost:8000/api/v1/webhooks/$ENDPOINT_ID
```

### List deliveries

`GET /api/v1/webhooks/deliveries` — `Permission.WEBHOOKS_READ`

Query: `endpoint_id` (UUID, optional), `status` (query name; the parameter is
`delivery_status` and is typed `WebhookDeliveryStatus`), `event_type`,
`start_date`, `end_date`, `page`, `per_page`. Response: `WebhookDeliveryList`
(`BaseList[WebhookDeliveryRead]`).

### Get delivery

`GET /api/v1/webhooks/deliveries/{delivery_id}` — `WEBHOOKS_READ` →
`WebhookDeliveryResponse` (alias of `WebhookDeliveryRead`): `webhook_endpoint_id`,
`event_type`, `payload`, `status`, `attempt_count`, `response_status`,
`response_body`, `next_retry_at`.

### Retry a failed delivery

`POST /api/v1/webhooks/deliveries/{delivery_id}/retry` — `WEBHOOKS_MANAGE` →
`WebhookDeliveryResponse`. Only a `failed` delivery is retryable; the guard
lives in `WebhookService.retry_delivery` (`ValidationError` otherwise), which
resets `status` to `pending`, `attempt_count` to 0 and `next_retry_at` to
`None`, then enqueues a fresh job.

### Test an endpoint

`POST /api/v1/webhooks/{endpoint_id}/test` — `WEBHOOKS_MANAGE`

Request `WebhookTestRequest`: `event_type` (str), `payload` (dict, default `{}`).
Response: `WebhookDeliveryResponse` — the test is recorded as a normal delivery
row and delivered by the normal pipeline. The per-tier rate limit is checked
**before** the endpoint lookup (`WebhookService.check_test_rate_limit`), so a
404 still consumes quota and the endpoint list cannot be enumerated by timing.

### Signing secret: view and rotate

- `GET /api/v1/webhooks/{endpoint_id}/signing-secret` — `WEBHOOKS_MANAGE` →
  `SigningSecretViewResponse`. The **plaintext is never returned**: the router
  masks it (`{first 6}…{last 4}`, or `******` for a short value). The
  plaintext is shown exactly once, at creation or rotation.
- `POST /api/v1/webhooks/{endpoint_id}/signing-secret/rotate` — `WEBHOOKS_MANAGE`.
  Request `SigningSecretRotateRequest`: `grace_period_seconds` (0–86400,
  default 300). Response `SigningSecretRotateResponse`:
  `webhook_endpoint_id`, `new_signing_secret` (plaintext), `rotated_at`,
  `old_secret_expires_at`, `grace_period_seconds`.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"grace_period_seconds":600}' \
  http://localhost:8000/api/v1/webhooks/$ENDPOINT_ID/signing-secret/rotate
```

## Module Structure

```
src/app/modules/webhooks/
├── __init__.py        # docstring only, no re-exports
├── router.py          # APIRouter(prefix="/webhooks", tags=["webhooks"])
├── service.py         # WebhookService: endpoints, cache, triggering, rotation
├── delivery.py        # enqueue / deliver / recover_stuck_deliveries + retry ladder
├── signer.py          # canonical bytes + t=,v1= signature scheme
├── utils.py           # SSRF validation, consumer-side verify, secret generation
├── http_client.py     # shared httpx client with SSRF-safe pinned-IP transport
├── models.py          # WebhookEndpoint, WebhookDelivery
├── schemas.py         # Pydantic request/response schemas
└── AGENTS.md          # This file
```

There is **no** `crud.py` in this module — the service issues its own
SQLAlchemy statements through `execute_scoped`.

## Delivery & retry behaviour

`delivery.py` owns the whole delivery lifecycle and is the only place that
decides "dead vs still pending".

### The retry ladder

`WEBHOOK_RETRY_DELAYS` (import-time snapshot of
`settings.webhooks.WEBHOOK_RETRY_DELAYS`, default `[60, 300, 900, 1800, 3600]`
= 1m, 5m, 15m, 30m, 1h) and the named constants `RETRY_DELAY_1_MINUTE`,
`RETRY_DELAY_5_MINUTES`, `RETRY_DELAY_15_MINUTES`, `RETRY_DELAY_30_MINUTES`,
`RETRY_DELAY_1_HOUR` live here. `service.py` re-exports those names for
backward compatibility.

Scheduling decisions go through `get_retry_delays()`, which reads the live
settings (the module constant is frozen at import time). `_handle_failure`
picks `delays[attempt_count - 1]`, applies ±10 % jitter
(`random.uniform(0.9, 1.1)`), rounds up (`math.ceil`, minimum 1 s), sets
`next_retry_at`, keeps the row `pending` and returns
`(delivery_id, delay, tenant_id)`. When the ladder is exhausted the row
becomes `failed` and no retry is scheduled.

### Per-endpoint circuit breaker

`deliver` obtains `get_webhook_breaker(str(endpoint.id))` — a breaker scoped
per endpoint, so one dead receiver cannot stall other tenants' endpoints
(`src/app/core/resilience/registry.py`; `get_webhook_breaker(None)` returns
the legacy process-global `webhook_circuit` in
`src/app/core/resilience/breakers.py`).

- `CircuitState.CLOSED` → admitted immediately, no probe.
- `CircuitState.HALF_OPEN` → exactly one delivery is admitted via
  `allow_single_flight_with_claim`; the others are deferred.
- `CircuitState.OPEN` → the delivery is **not** attempted: it is deferred by
  the breaker recovery window and **the attempt budget is not consumed**
  (so an open circuit can never exhaust the retry ladder without a single
  HTTP call). The probe slot is released on every exit path via
  `release_probe`.

### Send-time signing and idempotency

- The signature is computed **at send time**, per attempt, from the stored
  payload: `canonical_payload_bytes` + `sign_canonical_bytes` with a fresh
  timestamp. The transmitted bytes are exactly the signed bytes (the HTTP
  call uses `content=payload_bytes`, never `json=`).
- The active secret is `signing_secret` when present, otherwise the legacy
  `secret`.
- A delivery lock (`webhook:deliver_lock:{delivery_id}`, 60 s TTL, with an
  ownership token) serialises concurrent attempts, and a delivery already in
  a terminal state (`delivered` / `failed`) is never POSTed again.
- A non-2xx response records a breaker failure. A `4xx` other than `408` and
  `429` is a permanent failure with no retry; every other outcome (5xx,
  network error, timeout, circuit-open during the attempt) goes through
  `_handle_failure`.
- The retry enqueue (`_spawn_enqueue`) happens **after** the commit that
  persists `attempt_count` / `next_retry_at` / `status`, so a worker can never
  re-read a stale row and re-POST the same payload.

### Stuck-delivery recovery

`recover_stuck_deliveries(ctx)` is the safety net for a **lost enqueue**: a
`pending` row whose `coalesce(next_retry_at, created_at)` is older than
`settings.webhooks.WEBHOOK_RECOVERY_BUFFER_MINUTES` and for which no job is
running. It runs under `bypass_rls`, selects at most
`settings.webhooks.WEBHOOK_RECOVERY_BATCH_SIZE` rows `FOR UPDATE SKIP LOCKED`,
claims the delivery lock per row (an in-flight delivery makes the claim fail
and the row is skipped), releases the claim **before** enqueueing, and enqueues
`deliver_webhook_task` with `_job_id=f"recover_webhook_{id}"` so two ticks
cannot double-enqueue the same row. Each row commits or rolls back on its own,
so one bad row cannot discard the batch's progress.

## Signing

The wire contract lives in `signer.py`:

- `canonical_payload_bytes(payload)` → `json.dumps(payload, sort_keys=True).encode("utf-8")`.
  This exact byte sequence is what gets signed and what gets transmitted.
- `sign_canonical_bytes(canonical, secret, timestamp)` → `t={timestamp},v1={hex}`,
  where the signed message is `f"{timestamp}.".encode() + canonical`.
- `sign_payload(payload, secret, timestamp)` is the dict-level convenience
  wrapper; `hmac_sha256_hex(secret, data)` is the single HMAC implementation.
- Verification: `verify_signature(payload, secret, header, max_age_seconds)`,
  `verify_signature_raw(payload, secret, header, max_age_seconds)` and
  `canonical_bytes_from_raw(payload)`. A header carrying several `v1=`
  segments (rotation) verifies if **any** of them matches.
- `is_timestamp_fresh(timestamp, max_age_seconds, future_skew_seconds)`
  enforces the replay window; `DEFAULT_SIGNATURE_MAX_AGE_SECONDS` is 300.
- `utils.verify_webhook_signature(payload, signature, timestamp, secret, tolerance)`
  is the consumer-facing adapter. It accepts **only** the `t=,v1=` scheme and
  rejects the retired payload-only `sha256=` form (it logs
  `legacy_webhook_signature_rejected` so an unmigrated consumer is visible).

Rotation headers use the Stripe format: one timestamp, several signatures —
`t=<ts>,v1=<active>,v1=<old>` — while `old_secret_expires_at` is still in the
future. Do not emit a second `t=` segment.

## Configuration

`WebhookSettings` in `src/app/core/config/services.py`, exposed as
`settings.webhooks`:

| Key | Default | Meaning |
|---|---|---|
| `WEBHOOK_RETRY_DELAYS` | `[60, 300, 900, 1800, 3600]` | retry ladder, in seconds |
| `WEBHOOK_HTTP_TIMEOUT_SECONDS` | `30.0` | total HTTP timeout |
| `WEBHOOK_HTTP_CONNECT_TIMEOUT_SECONDS` | `10.0` | connect timeout |
| `WEBHOOK_SIGNATURE_HEADER` | `"X-Webhook-Signature"` | header name (declared, not read by the module) |
| `WEBHOOK_SIGNATURE_MAX_AGE_SECONDS` | `300` | replay window (1–3600) |
| `WEBHOOK_SIGNATURE_ALGORITHM` | `"sha256"` | algorithm (declared, not read by the module) |
| `WEBHOOK_RECOVERY_BATCH_SIZE` | `100` | rows per recovery sweep |
| `WEBHOOK_RECOVERY_BUFFER_MINUTES` | `5` | age before a pending row counts as stuck |

Plus `settings.app.WEBHOOK_ALLOW_INTERNAL_URLS` (default `False`) in
`src/app/core/config/app.py`: it relaxes the private-IP / hostname checks only —
the HTTPS requirement is always enforced, and the kill switch does **not**
disable SSRF validation.

`WEBHOOK_SIGNATURE_HEADER`, `WEBHOOK_SIGNATURE_MAX_AGE_SECONDS` and
`WEBHOOK_SIGNATURE_ALGORITHM` are declared in the settings class but are not
read anywhere in `src/app` — the module hard-codes the header names
(`X-Webhook-Signature`, `X-Webhook-Event`, `X-Webhook-ID`,
`X-Webhook-Timestamp`) and uses `DEFAULT_SIGNATURE_MAX_AGE_SECONDS` from
`signer.py`. Do not assume changing those settings changes the wire format.

## Background jobs

- `deliver_webhook_task` (`src/app/core/worker/functions.py`) is the ARQ job
  enqueued by `delivery.enqueue`. It coerces the `delivery_id` back to a UUID
  and runs the delivery inside the tenant RLS context.
- `recover_stuck_deliveries` is registered as a periodic ARQ cron in
  `src/app/worker.py` (`FeatureTask(recover_stuck_deliveries, _EVERY_FIVE_MINUTES)`,
  every five minutes). The same function can be run manually with
  `uv run python -m src.scripts.recover_webhooks`.
- `delivery.enqueue` enqueues the job `"deliver_webhook_task"` through the ARQ
  pool; `delivery._spawn_enqueue` is the fire-and-forget wrapper used from
  inside a running loop (it goes through `create_safe_task`).
- `handle_webhook_trigger_event` and `webhook_event_adapter` in `service.py`
  are the event-bus entry points, registered in `src/app/events.py` for
  `"webhook.trigger"` and for the business events (`user.created`,
  `user.updated`, `user.deleted`, `sso.login`, `auth.login`, `auth.logout`,
  `auth.mfa.enabled`, `auth.mfa.disabled`). `webhook_event_adapter` **must**
  stay a module-level function: the worker re-imports it dynamically by
  `module` + `name`, so a closure would fail in the worker.
- `handle_webhook_trigger_event` re-raises on failure on purpose — swallowing
  the error defeats the bus's retry / dead-letter routing.

## Patterns & Conventions

### DO: trigger through the service, never by hand-inserting deliveries

```python
from src.app.modules.webhooks.service import WebhookService

service = WebhookService(db)
deliveries = await service.trigger_webhooks(tenant_id, "user.created", payload)
```

`trigger_webhooks` resolves the matching endpoints through
`get_active_endpoints` (Redis-cached, glob patterns supported via
`_glob_match`), bulk-inserts the delivery rows with `signature` left unset,
and enqueues one ARQ job per row.

### DO: sign and send through `signer.py`

```python
from src.app.modules.webhooks.signer import canonical_payload_bytes, sign_canonical_bytes

payload_bytes = canonical_payload_bytes(payload)
header = sign_canonical_bytes(payload_bytes, secret, timestamp)
```

### DON'T: add a second HMAC implementation

The signer owns the canonical byte sequence and the digest. A local replica in
a service or a test drifts from the wire contract and silently breaks consumer
verification.

### DON'T: re-parent an endpoint through `update_endpoint`

`WebhookService.update_endpoint` applies a fixed allowlist; a field outside it
is ignored on purpose.

### DON'T: compute a signature at trigger time

The signature is computed at send time in `delivery.py`, per attempt, from the
stored payload. The `WebhookDelivery.signature` column is a deprecated
trigger-time artifact and stays `NULL`; it is never the wire value.

### DO: keep the SSRF path intact

Endpoint URLs are validated with `validate_url_ssrf` (create/update) and with
`validate_webhook_url` at send time; the resolved IP is pinned through
`extensions={"resolved_ip": ...}` by `SSRFSafePoolTransport`, which also
preserves the `Host` header and the SNI hostname.

## Touch Points / Key Files

- Service: `src/app/modules/webhooks/service.py` — `WebhookService`
- Delivery pipeline: `src/app/modules/webhooks/delivery.py` — `enqueue`,
  `deliver`, `recover_stuck_deliveries`, `get_retry_delays`
- Signing contract: `src/app/modules/webhooks/signer.py`
- SSRF + consumer verification: `src/app/modules/webhooks/utils.py`
- HTTP client: `src/app/modules/webhooks/http_client.py` —
  `get_webhook_http_client`, `SSRFSafePoolTransport`
- Models: `src/app/modules/webhooks/models.py` — `WebhookEndpoint`,
  `WebhookDelivery`, `WebhookDeliveryStatus`
- Schemas: `src/app/modules/webhooks/schemas.py`
- Permissions: `src/app/core/rbac/permissions.py` — `Permission.WEBHOOKS_*`
- Breaker registry: `src/app/core/resilience/registry.py` —
  `get_webhook_breaker`
- Worker wiring: `src/app/worker.py`, `src/app/core/worker/functions.py`
- Event wiring: `src/app/events.py`
- Settings: `src/app/core/config/services.py` (`WebhookSettings`),
  `src/app/core/config/app.py` (`WEBHOOK_ALLOW_INTERNAL_URLS`)

## JIT Index Hints

```bash
# Routes and their permissions
rg -n "router\.(get|post|patch|delete)|requires_permission" src/app/modules/webhooks/router.py

# The retry ladder and the failure handler
rg -n "WEBHOOK_RETRY_DELAYS|get_retry_delays|_handle_failure" src/app/modules/webhooks/

# The circuit breaker usage
rg -n "get_webhook_breaker|allow_single_flight_with_claim|release_probe" src/app/modules/webhooks/

# The signing contract
rg -n "canonical_payload_bytes|sign_canonical_bytes|verify_signature" src/app/modules/webhooks/

# The recovery cron
rg -n "recover_stuck_deliveries" src/app/ src/scripts/

# Who triggers webhooks
rg -n "trigger_webhooks|handle_webhook_trigger_event|webhook_event_adapter" src/app/
```

## Common Gotchas

- **One endpoint, one event-type pattern.** An endpoint subscribes to a single
  `event_type` string, which may be a glob (`user.*`, `user.?reated`,
  `user[0-9]`). There is no `event_types` list. Matching happens in
  `get_active_endpoints` via `_glob_match`, whose compiled-regex cache
  (`_FN_REGEX_CACHE`) is bounded at `_FN_REGEX_CACHE_MAX` (1024) because the
  patterns are tenant-supplied.
- **The endpoint cache is invalidated on write.** `create_endpoint`,
  `update_endpoint`, `delete_endpoint` and `rotate_signing_secret` call
  `_invalidate_endpoints_cache`, which deletes the per-tenant index
  `webhook:tenant_endpoints:{tenant_id}` and every key in it. Entries are
  cached for 300 s under `webhook:active_endpoints:{tenant_id}:{event_type}`.
- **The plaintext secret exists exactly once.** It is returned by the create
  and rotate responses. `WebhookEndpointRead` has no secret field, and the
  signing-secret view endpoint returns a masked preview.
- **Rotation keeps the old secret alive for the grace window.**
  `rotate_signing_secret` moves the current secret to `old_signing_secret`,
  writes the new one, and sets `old_secret_expires_at = rotated_at +
  grace_period_seconds`. While that deadline has not passed, `deliver` appends
  a second `v1=` segment for the old secret. Note that the rotate response
  *message* still says the old secret is invalidated immediately — the
  behaviour above is what the code does.
- **A decryption failure must not leak the ciphertext.**
  `get_signing_secret` raises instead of returning the encrypted blob.
- **A circuit-open skip is not an attempt.** `attempt_count` is not
  incremented when the breaker defers a delivery, so the retry budget
  survives a long outage.
- **A `4xx` other than `408` / `429` is permanent.** It is marked `failed`
  immediately and is not retried.
- **Manual retry only applies to `failed` deliveries.**
  `retry_delivery` raises `ValidationError` for any other status.
- **The test endpoint is rate limited per tier.** `check_test_rate_limit`
  resolves the limit from the tier entitlement (`resolve_path_limit` on
  `/api/v1/webhooks/{endpoint_id}/test`, default `webhook_test_limit` 10 per
  `webhook_test_period` 60 s) and consumes it through the shared rate-policy
  seam, raising `RateLimitExceededError` (HTTP 429).
- **Column name:** the endpoint JSONB column is `endpoint_metadata`, not
  `metadata` — `metadata` is reserved by SQLAlchemy.

## Pre-PR Checks

```bash
uv run pytest tests/unit/modules/webhooks/ tests/integration/modules/webhooks/ -v && \
uv run mypy src/app/modules/webhooks/ && \
uv run ruff check src/app/modules/webhooks/
```

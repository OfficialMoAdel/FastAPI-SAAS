# CONTEXT — FastAPI_saas domain language

Live glossary for architecture work. Terms here name good Seams; use them
instead of inventing synonyms. (Created lazily during the Tenant-scoped
persistence grilling loop, 2026-09-22.)

## Terms

- **Tenant-scoped persistence** — the invariant that every read/write on a
  `tenant_id`-bearing table is scoped to the active tenant context, fail-closed
  without one. One Seam owns the rule: `scoped_crud()` and
  `TenantAwareCRUD` cover CRUD; `execute_scoped()` covers hand-built
  `SELECT` / `UPDATE` / `DELETE` statements and owns the database guard; and
  `stream_scoped()` keeps that guard active for streamed `SELECT` results.
  `TenantAwareCRUD.create()` and `update()` use the same scope decision as
  reads: an explicit tenant scope is valid for workers, request context wins
  on mismatch, and unscoped writes fail closed. The `_allow_unscoped` /
  `allow_unscoped` escape hatch remains explicit and logged for documented
  read/system paths. Global paths and multi-model dashboard subqueries are
  intentional exceptions. Seam: `src/app/core/tenancy/` +
  `src/app/core/db/scoped.py` + `src/app/core/db/tenant_crud.py`. See ADR-0005
  (tenant-only RLS), ADR-0006 (one `resolve_tenant_id` Seam).

- **Admission** — the per-request decision `RateLimitMiddleware` makes *before*
  routing: let through, limit, or reject. Emits an `AdmissionOutcome` whose
  `action` is `passthrough` | `admit` | `limited` | `reject_banned` |
  `reject_auto_ban`. Admission owns the ban boundary and auto-ban escalation;
  it does NOT own counter arithmetic. Seam: `RequestAdmission.evaluate(request)`
  in `src/app/core/security/request_admission.py`, injected into the middleware
  (one production Adapter, test doubles are the second).

- **Verdict** — `RateLimitVerdict`: the outcome of ONE rate-limit counter check
  (`is_limited`, limit/period/remaining, `retry_after`, bucket key, HTTP
  headers). A Verdict is not an Admission — Admission adds ban and auto-ban
  semantics on top of one. Both paths (per-request and Security budget) must
  assemble a Verdict in exactly one place.

- **Security budget** — an identity-keyed brute-force budget (`auth`,
  `mfa_verify`, `mfa_mutate`, `passkey_login`, `magic_link_send`, `ai_chat`,
  `refresh`) checked *after* routing, at the endpoint layer, once the identity
  is known. Keys on identity, not path. Contrast **Path policy** — the
  `(limit, period)` resolved for a path + tier, checked at the edge before
  routing. Same Verdict Seam underneath, different when and what they key on.
  `src/app/core/security/budgets.py` owns it end to end: the door supplies a
  `SecurityAction` plus an `Identity` of *facts* (user_id / ip / token /
  email_hash) and the Module derives the legs, the key namespace, the
  fail-closed policy, and the refusal. The key namespace comes from
  `leg.budget`, never from the action and never from caller input, so a door
  cannot assemble or re-key a meter. Two entry points: `check` raises the one
  `RateLimitExceededError`, `probe` returns the Verdict for the two callers
  whose transport response is shaped by the numbers (admission, AI Depends).
  MFA meters were separated into three declared actions — step-up verify
  (`POST /mfa/verify`, account), pending token (`POST /mfa/complete-login`,
  pre-peek), and per-account global (post-peek) — on 2026-09-26; **no key
  bytes changed**, the settings-sniff branch that chose the account meter's
  name at runtime is retired, and `validate_budget_definitions()` fails boot
  if a leg names an unconfigured budget. The three doors are separate limits
  and must never be collapsed into one: they are metered independently and
  converge only on the durable Lua lockout. The lockout is *declared* on the
  two verification actions, not composed, and contract tests pin that both
  doors still route through it.

- **Ban vs blacklist** — different things; do not conflate. **ban** = an IP
  rejected outright for a duration, escalating via auto-ban after N violations;
  a banned IP is rejected even when whitelisted. **blacklist** = a revoked
  token; a blacklisted token is treated as anonymous and loses its WHOLE tier
  identity (including `tier_id`) so it cannot keep premium per-path policy.

- **Bypass** — an operator-declared whitelisted IP skips the counter but never
  the ban. No quota counter ran, so `RateLimitVerdict` limit/period/remaining
  are `None` and no numeric `X-RateLimit-*` quota headers are emitted. The one
  canonical signal is `X-RateLimit-Bypassed: true`.

- **Principal** — the identity behind one request, resolved exactly once and
  never re-derived. `resolve_principal(request, db, policy, …)` in
  `src/app/api/deps/auth.py` is the ONE entry point; bearer-token and API-key
  are Adapters over it. `AuthContext` (`user` + `token_data` + `tenant`) is the
  result type and lives in `src/app/core/principal.py` so services can import
  it cycle-free. `PrincipalPolicy` (`REQUIRED` / `OPTIONAL`) is one named value
  replacing the `strict: bool` soup, and presence is the ONLY thing it relaxes
  — a *presented* credential that fails always refuses (C3). The tenant half
  has one source too: RBAC no longer queries `Tenant` from
  `request.path_params`, it passes `explicit_tenant` into the seam and lets
  `resolve_tenant_id` resolve it (ADR-0006).
  Seam: `src/app/core/principal.py` (relocating the type here is what deletes
  the `core.rbac.dependencies -> api.deps` edge).

- **Denial** — the single 401/403/None decision for Principal resolution:
  `DENIAL_TABLE: Mapping[DenialReason, Denial]` in `src/app/core/principal.py`,
  read only through `denial(reason, **fmt)`. Every refusal — both bearer flows,
  the API-key flow, RBAC's gate, the superuser gate — resolves its exception
  class and message from one row, so the nine entry points cannot disagree
  about which status a condition produces. `INACTIVE_SUPERUSER` is a deliberate
  divergence row (403, not 401) kept named rather than hidden in a second raise
  site; messages stay byte-identical to the pre-table raises. An unmapped
  reason fails loud (`KeyError`) — a missing row is a bug, not a policy
  default.

- **Runtime composition** — the ordered phase plan and concrete application
  startup/teardown wiring. `compose_phases` in `src/app/core/runtime.py` is the
  reusable mechanism; `src/app/composition.py` owns the web phase rows and
  FastAPI assembly; the worker registry remains its own process composition
  root. Core setup owns reusable infrastructure hooks, not feature wiring.
  Seam: `StartupPhase` + `compose_phases`.

- **Readiness** — the single traffic-gating answer across database, Redis,
  queue, and EventBus registration. Only the all-healthy state returns HTTP
  200; degraded dependency state remains visible in the body but returns HTTP
  503. `/api/v1/ready` is canonical; `/api/health/ready` is a compatibility
  alias using the same dependency policy. Seam: `resolve_readiness_status()` in
  `src/app/core/health.py`.

- **Event registration** — the application wiring step that supplies the
  complete handler batch to `EventBus.register_handlers()`. `EventBus` owns the
  private registry, readiness flag, reset, and handler count; application code
  supplies handlers and tests observe behavior through the public interface.
  Seam: `src/app/events.py` + `src/app/core/events/bus.py`.

- **Resource ownership** — the framework-independent policy that a
  non-superuser actor may act only on resources whose owner matches. The policy
  lives in `src/app/core/security/ownership.py`; API request context re-exports
  it for compatibility, while feature services import the core policy directly.
  Seam: `owns_resource` / `require_resource_ownership`.

- **Principal provider** — the DIP hook that lets core *own* the Principal
  dependency while api *implements* it: `PrincipalProvider` Protocol +
  `register_principal_provider()` in `core/principal.py`, wired at the bottom
  of `api/deps/auth.py`, with `ensure_principal_provider_registered()` run as
  `StartupPhase("principal_gate", …)` in `core/setup.py` so missing wiring
  fails fast at boot instead of surfacing as mass 401s. Same shape as
  `core/security/jwt_service.py`. Required because `resolve_principal` imports
  the module facades (`users`, `api_keys`) — which core may not.

- **Issuance** — the post-authentication choreography owned by `TokenIssuer`:
  claims → token pair → refresh cookie → session registration → login audit.
  Every login door (password, passkey, magic-link, SSO, MFA completion) ends
  here; no door writes cookies or registers sessions itself.

- **Session rotation** — the refresh-token counterpart of Issuance, owned by
  the same Seam (`TokenIssuer.rotate_session`): distributed lock → rotated
  pair (sid/absolute_exp/iat preserved, never extended) → old-token blacklist
  → rotation marker → session invalidate + re-register → cookie → audit.
  Verification pre-checks (reuse detection, token verify, absolute-exp,
  user-active, tenant bind) stay in `AuthService`; everything after a verified
  payload is the Seam's. One ownership rule: exactly one cookie write, exactly
  one session registration, on both login and refresh paths.

- **Payment gateway** — the Stripe Seam owned by `src/app/clients/billing/`
  (`PaymentGateway` Protocol, `StripeGateway` Adapter): key resolution
  (per-tenant SELECT + decrypt + miss-cache + mismatch fail-closed + global
  fallback), transient-only retry, per-call idempotency keys. The module side
  (`subscription_service.retrieve_subscription_for_tenant`) owns resolve →
  build → retrieve as ONE call so the resolved key never crosses a module
  boundary as a naked string; the Adapter never leaves `clients/`.
- **Reject catalog** — the owned table of middleware rejection rows
  (`REJECT_CATALOG` in `src/app/core/errors.py`): every guard rejection
  (rate_limit, tenant, body_limit, attack_detection, ip_filter) resolves
  code/message/status/headers from one row via `middleware_reject(kind=...)`
  instead of hand-building envelopes. Strings are verbatim what the guards
  previously built; header math (Retry-After, x-max-body-size) lives in
  row builders over primitives. Unknown kinds fail loud (`ValueError`).
  Seam: `middleware_reject` in `src/app/core/errors.py`; the old
  `middleware_error_response` remains as the envelope builder underneath.

- **HTTP presentation** — how a domain exception presents at the API boundary,
  declared **beside the exception** instead of as a row in core. `AppException`
  carries `http_status` / `response_code` / `public_message` / `log_message` /
  `log_error` plus two overridable payload hooks, `http_details()` (the envelope
  `details`) and `http_headers()` (transport headers *derived* from the
  exception's state — `RateLimitExceededError` turns `retry_after` into
  `Retry-After`). ONE generic handler in `src/app/core/errors.py` resolves them
  through `resolve_http_status()` / `resolve_response_code()` /
  `resolve_public_message()` / `http_details()` / `http_headers()`.
  Static families declare the attributes (`StorageBackendError.http_status = 502`);
  a family whose status is chosen per *instance* overrides
  `resolve_http_status()` / `resolve_response_code()` instead (`AuthError` and
  `StripeError` derive both from `self.status_code` — a method, not a property,
  because mypy forbids narrowing the writable `ClassVar` to a read-only property).
  `response_code` is the envelope `error.code` — deliberately NOT `error_code`,
  which already means a *provider* code on `AIProviderError` /
  `PaymentRequiredError` and surfaces in `details["error_code"]`.
  `public_message` overrides the instance message when the public string must
  differ from the internal one (non-leaking policy); `UnauthorizedError` goes
  further and declares **no** `http_details()`, so its `reason` never reaches
  the client even though `ForbiddenError`'s does.
  ONE source of truth as of candidate 3: there is **no mapping table**. Storage,
  billing, auth, ai, shared and user_preferences all declare their own mapping
  beside the exception, so every domain error resolves through the single
  generic handler. A handler registered for a *more specific* class still wins
  over it, because Starlette resolves by walking `type(exc).__mro__` and taking
  the first registered class (`CacheException` relies on this for its
  deliberate 500).
  A whole tree migrates with one declaration: `AIProviderError` states the
  mapping once and its 12 subclasses inherit it — exactly what the single core
  row used to do via the MRO walk. `AIServerError.status_code` is the
  *provider's* status, carried for diagnostics only and deliberately NOT
  honoured as the envelope status (it stays the tree's static 502).
  Seam: the `AppException` presentation attributes + `_make_app_exception_handler`
  in `src/app/core/errors.py`. `core.errors` imports **no feature module at all**,
  which is what finally cleared the last `core.errors -> modules.*` edge from
  the H-5 import contract.

- **Dead-letter queue** — the shared failure queue at `arq:dlq` for ARQ job
  failures and EventBus handler failures. `DeadLetterQueue` owns the envelope
  codec, `arq` / `event_bus` source semantics, bounded retention, retry/clear,
  and legacy-key migration. `EventBus` and `JobMonitoringService` delegate
  their queue operations to this module; the monitor alone decodes legacy
  pipe-delimited rows for administrative compatibility. Seam:
  `src/app/core/dead_letter.py`.

- **IP rules** — the request-guard policy for IP allow/deny reads and writes.
  `IpRules` is the injected Seam used by IP filtering and attack detection;
  `RedisIpRules` is the production Adapter backed by the core-owned
  `IPFilterService`. Seam: `src/app/core/guard/ip_rules.py`. The security
  module keeps no facade or re-export: its `service.py` was deleted
  (2026-09-26) and the router imports the core-owned instance directly.

- **L1 invalidation seam** — the process-local cache Seam: `L1Cache(name,
  channel, codec, caches=[...])` in `src/app/core/cache/l1.py` owns the whole
  invalidation half of a per-process `TTLCache` key-space. Five sit behind it:
  `tenant_l1` (60s, plus its parsed-object memo), `ban_l1` (5s),
  `rate_limit_policy_l1` (300s, plus its compiled path map), `bitmask_l1` (30s)
  and `auth_info_l1` (8s). It owns the three things each owner used to re-derive
  by hand: the **broadcast token** (`"*"`, and `""` for the two owners that treat
  empty as broadcast), the **drop-local-then-publish ordering** (pub/sub does not
  echo to the publisher, so publishing first leaves this process serving the
  entry it just invalidated), and the **companion-lockstep rule** — passing every
  layer in `caches` makes "a parsed entry can never outlive the dict it was built
  from" structural instead of a comment.
  Only the *key grammar* varies, and it is a declaration: `RawKeyCodec` (the
  payload IS the cache key), `PrefixedKeyCodec` (the payload omits a prefix — the
  ban cache is keyed `ban:{ip}` while its channel carries a bare IP),
  `IntKeyCodec` (fail-**open**: a malformed tier id becomes `IGNORE`, so it
  cannot flush the fleet's policy cache) and `PairKeyCodec` (fail-**safe**: an
  unparseable bitmask payload broadcasts, so it cannot leave a revoked permission
  cached). Those two opposite policies are deliberate and pinned by test.
  It also owns the **registry**: `L1Cache.__init__` self-registers, so
  `import_l1_owner_modules()` + `start_all_l1_listeners()` are the single
  lifecycle entry point. The five-line import list inside
  `import_l1_owner_modules` is the one remaining enumeration and stays explicit —
  Python has no reliable auto-discovery, and scanning would reintroduce the silent
  publish-into-the-void failure the registry exists to prevent.
  What it does NOT own: TTLs, `maxsize` values, the eventual-consistency bound
  (ADR-0004 keeps those by decision), or the read/write path — it *wraps* the
  existing `TTLCache` objects, so `get`/`put` stay direct with no hot-path
  indirection.
  Seam: `L1Cache` in `src/app/core/cache/l1.py`.

- **Transfer/Admin split** — the storage Seam: `StorageService` owns file
  transfer (upload/download/delete/quota/metrics) and consumes a narrow
  `ResolvedStorage` policy object (provider + provider_name + size/type/count/
  quota limits); `StorageAdminService` (in `storage.admin`) owns tenant
  settings, credential decrypt, and the provider cache, assembling the object
  via `resolve(tenant_id)`. Transfer never touches the settings row or
  decrypt; settings/credential/quota-policy fixes land on one side only.
  Seam: `StorageService(db, admin=None)` constructor injection in
  `src/app/modules/storage/service.py`.

- **Storage provider construction** — the OUTBOUND half of the storage Seam,
  and a decided seam of its own: `src/app/clients/storage/` owns the
  `StorageProvider` interface, the three real vendor adapters (`S3Storage`,
  `AzureBlobStorage`, `LocalStorage`), the `StorageProviderType` vendor
  vocabulary, and `StorageProviderRegistry` (the factory map, its
  `register`/`unregister`/`reset` test-override path, and the credential ->
  constructor mapping per vendor). That package imports **no feature module**;
  `src/app/modules/storage/` imports downward into it. This was the inversion
  card 16 removed: the client package used to import four feature modules
  (`s3_storage` / `azure_storage` / `local_storage` / `interfaces` plus the
  `models` enum) while `StorageAdminService` reached back into the registry to
  build a provider.
  What it does NOT own: tenant settings, credential decrypt, the provider
  cache, and the `close()`/dispose choreography — those stay in
  `StorageAdminService`, which is the only caller of the registry and names a
  provider by `StorageProviderType` alone. Quota / ownership / tenant policy
  stays on the feature side; the adapters know none of it.
  `StorageProviderType` is DEFINED in `clients/storage/provider_type.py` (a
  leaf, no project imports) and re-exported by `modules/storage/models.py` for
  the `StorageSettings` column default, so one object serves both sides and
  `src.app.models` keeps re-exporting it unchanged. The schema enum in
  `modules/storage/schemas.py` is deliberately a DIFFERENT, Pydantic-facing
  type and is not merged with it.
  Why `clients/storage/__init__.py` resolves the registry and the adapters
  through `__getattr__` instead of importing them: `modules/storage/models.py`
  imports `StorageProviderType`, so an eager registry import would re-create
  the cycle `clients.storage -> registry -> s3_storage -> modules.storage ->
  models -> clients.storage.provider_type` and would put boto3/azure on the
  model-import path. Same lazy-package pattern as
  `modules/storage/__init__.py` and `core/resilience/breakers.py`.
  Seam: `StorageProviderRegistry` in `src/app/clients/storage/registry.py`.

- **Delivery split** — the webhook Seam: `delivery.deliver` /
  `delivery.enqueue` in `src/app/modules/webhooks/delivery.py` own the
  whole delivery lifecycle (lock → sign → send → backoff → DLQ/metrics);
  `WebhookService` owns endpoint CRUD + `trigger_*` fan-out + the
  endpoint-side `retry_delivery` state reset, calling `delivery.enqueue`
  direct. ARQ workers call `deliver(db, delivery_id)` with no service
  between. The deleted `deliver_webhook` / `_enqueue_delivery`
  pass-throughs must not come back.
  Seam: `deliver` / `enqueue` in `src/app/modules/webhooks/delivery.py`.

- **Breaker** — a named `CircuitBreaker` singleton protecting one outbound
  dependency, owned by `src/app/core/resilience/breakers.py`: `webhook`,
  `email`, `arq_queue`, plus `database` and `token_cache_circuit` (constructed
  by their own modules). The admin panel observes and resets breakers
  **through the registry only**, so a breaker that is not registered is
  invisible to operations — it cannot be seen or reset during an incident.
  The invariant is therefore "a named breaker is built by
  `create_circuit_breaker()`, which registers as a side effect of
  construction", not "someone remembered to register it". A bare
  `CircuitBreaker(...)` is for tests and per-endpoint scopes; it is not a
  named production breaker.
  What it does NOT own: the algorithm (`core/utils/circuit_breaker.py`), the
  DB-domain layer built on top of it (`core/resilience/db_circuit_breaker.py`),
  or the registry itself. Construction lives in `breakers.py` rather than
  beside the algorithm because `registry.py` imports
  `core/utils/circuit_breaker.py` at module level, so that module cannot import
  the registry back — the cycle that made the old `_register_default_breakers()`
  a `pass` with a commented-out call. `core.utils.circuit_breaker` re-exports
  the names lazily (`__getattr__`, the `modules/tenants/__init__.py` pattern),
  so import sites are unchanged and object identity is preserved.
  Seam: `src/app/core/resilience/breakers.py`.

- **Default breaker set** — the declared table (`default_breakers()`) that
  `reset_registry()` replays whenever it recreates the registry. It exists
  because clearing the registry used to drop every registration with it, and
  `tests/conftest.py` compensated by hand-registering `database` back — the
  suite manufacturing an invariant production did not own. With the replay the
  set is a property of the registry rather than of import order, and deleting
  that hand-registration is what proves the constraint is gone.
  `reset_registry()` also clears `_scoped_webhook_breakers`: those are
  registry-owned, and leaving them cached while their registrations were
  dropped would hand back invisible breakers.
  What it does NOT own: thresholds, recovery timeouts, or which scopes exist —
  those stay with each breaker.
  Seam: `default_breakers()` in `src/app/core/resilience/breakers.py`.

- **Startup phases** — the boot Seam: `StartupPhase(name, startup, teardown)` rows in `src/app/core/setup.py` own one boot step each; `compose_phases` runs startups in list order, then reverse teardowns + the `tail` list.
  Order is data — reordering boot means reordering rows, not editing a
  130-line closure. The factory runs full teardown even when a startup
  step raises (`teardown_on_startup_failure=True`, legacy try/finally
  semantics); `lifespan_with_admin` runs its tail only on the clean path.
  Seam: `compose_phases` in `src/app/core/setup.py`.

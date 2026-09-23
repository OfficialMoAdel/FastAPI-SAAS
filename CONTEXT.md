# CONTEXT — FastAPI_saas domain language

Live glossary for architecture work. Terms here name good Seams; use them
instead of inventing synonyms. (Created lazily during the Tenant-scoped
persistence grilling loop, 2026-09-22.)

## Terms

- **Tenant-scoped persistence** — the invariant that every read/write on a
  `tenant_id`-bearing table is scoped to the active tenant context, fail-closed
  without one. One Seam owns the rule; `TenantAwareCRUD` (CRUD path) and
  `tenant_scope()` (hand-built `select()` path) are Adapters over it. Escape
  hatch (`_allow_unscoped` / `allow_unscoped`) is explicit and logged.
  Seam: `src/app/core/tenancy/` + `src/app/core/db/tenant_crud.py`
  (to be unified). See ADR-0005 (tenant-only RLS), ADR-0006 (one
  `resolve_tenant_id` Seam).

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

- **Ban vs blacklist** — different things; do not conflate. **ban** = an IP
  rejected outright for a duration, escalating via auto-ban after N violations;
  a banned IP is rejected even when whitelisted. **blacklist** = a revoked
  token; a blacklisted token is treated as anonymous and loses its WHOLE tier
  identity (including `tier_id`) so it cannot keep premium per-path policy.

- **Bypass** — an operator-declared whitelisted IP skips the counter but never
  the ban. Currently signalled by a sentinel `limit=999999` plus
  `bypassed=True`; the sentinel is leakage and should not spread.

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

- **Transfer/Admin split** — the storage Seam: `StorageService` owns file
  transfer (upload/download/delete/quota/metrics) and consumes a narrow
  `ResolvedStorage` policy object (provider + provider_name + size/type/count/
  quota limits); `StorageAdminService` (in `storage.admin`) owns tenant
  settings, credential decrypt, and the provider cache, assembling the object
  via `resolve(tenant_id)`. Transfer never touches the settings row or
  decrypt; settings/credential/quota-policy fixes land on one side only.
  Seam: `StorageService(db, admin=None)` constructor injection in
  `src/app/modules/storage/service.py`.

- **Delivery split** — the webhook Seam: `delivery.deliver` /
  `delivery.enqueue` in `src/app/modules/webhooks/delivery.py` own the
  whole delivery lifecycle (lock → sign → send → backoff → DLQ/metrics);
  `WebhookService` owns endpoint CRUD + `trigger_*` fan-out + the
  endpoint-side `retry_delivery` state reset, calling `delivery.enqueue`
  direct. ARQ workers call `deliver(db, delivery_id)` with no service
  between. The deleted `deliver_webhook` / `_enqueue_delivery`
  pass-throughs must not come back.
  Seam: `deliver` / `enqueue` in `src/app/modules/webhooks/delivery.py`.

- **Startup phases** — the boot Seam: `StartupPhase(name, startup, teardown)` rows in `src/app/core/setup.py` own one boot step each; `compose_phases` runs startups in list order, then reverse teardowns + the `tail` list.
  Order is data — reordering boot means reordering rows, not editing a
  130-line closure. The factory runs full teardown even when a startup
  step raises (`teardown_on_startup_failure=True`, legacy try/finally
  semantics); `lifespan_with_admin` runs its tail only on the clean path.
  Seam: `compose_phases` in `src/app/core/setup.py`.

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

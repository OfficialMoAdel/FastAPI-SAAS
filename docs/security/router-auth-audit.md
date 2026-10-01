# Router auth audit — the twelve never-read routers

**Scope.** `modules/ai`, `posts`, `security`, `storage`, `notifications`,
`webhooks`, `feature_flags`, `billing`, `tiers`, `rate_limits`,
`user_preferences`, `tasks`. (`billing` and `storage` ship sub-routers; all
five billing files and both storage files are covered.) `users`, `rbac`,
`api_keys` and `tenants` were read before and are out of scope here.

**Tool.** [`scripts/audit_router_auth.py`](../../scripts/audit_router_auth.py) —
AST, not text. Re-runnable:

```bash
python scripts/audit_router_auth.py            # table + every flagged route
python scripts/audit_router_auth.py --json     # machine-readable
python scripts/audit_router_auth.py --selftest # falsifiability proof
```

**Bottom line.** 117 routes across 15 router files. 94 are clean by the
detector. The detector raised **23 hits**; reading every one produced **2 real
findings** and **21 explained false positives**. No unauthenticated mutating
route exists anywhere in these twelve modules — the four anonymous routes are
all reads of a global catalog, plus one signature-authenticated webhook.

---

## Why a script and not a grep

Auth here is expressed in four idioms, and each can sit in either of two
positions (handler parameter, or the decorator's `dependencies=[...]`):

| # | Idiom | Example site |
|---|-------|--------------|
| 1 | `ctx: RequestContextDep` | `ai/router.py:66`, `billing/router.py:331` |
| 2 | `Depends(get_current_user)` | `routers/checkouts.py:34`, `routers/invoices.py:26` |
| 3 | `Depends(get_current_superuser)` | `tenants/router.py` (4 sites) |
| 4 | `Depends(requires_permission(Permission.X))` | `billing/router.py` (10 sites) |

The prior grep-based scan reported `checkouts.py` and `invoices.py` as having
**zero** auth references. That is idiom 2 in parameter position — the pattern
simply omitted it. Idioms 2 and 3 appear both as parameters and inside
`dependencies=[...]`, so a pattern must handle both positions or it will
manufacture findings on a payment surface.

### A fifth shape the first script run also got wrong

The first AST run flagged `POST /ai/sessions/{session_id}/chat` and its
streaming twin as **UNAUTHENTICATED**. They are authenticated. The parameter is
`current_user: RateLimitedUser`, and:

```python
# src/app/api/deps/ai_rate_limit.py:85
RateLimitedUser = Annotated[AuthenticatedUser, Depends(check_rate_limit_dependency)]
# check_rate_limit_dependency's own parameter:
current_user: Annotated[AuthenticatedUser, Depends(get_current_user)]
```

The route mentions none of the four idioms. Authentication arrives through a
**wrapper alias two hops away**, across a module boundary. The detector now
resolves that hop (`DependencyResolver`): local aliases, imported symbols, and
the callee's own parameters, memoised per `(module, symbol)`. This is the same
failure class as the grep false negative, one level deeper — a detector that
stops at literal names will always produce false "unauthenticated" findings on
wrapped dependencies.

---

## Per-router results

| module | routes | unauth | partial | ok | confirmed finding |
|---|---:|---:|---:|---:|---|
| ai | 11 | 0 | 6 | 5 | — (all 6 self-scoped) |
| billing | 35 | 1 | 4 | 30 | **P3** — Stripe webhook, by design |
| feature_flags | 5 | 0 | 0 | 5 | — |
| notifications | 6 | 0 | 0 | 6 | — |
| posts | 6 | 0 | 3 | 3 | — (all 3 owner-checked) |
| rate_limits | 5 | 2 | 0 | 3 | **P2** — anonymous tier policy read |
| security | 16 | 0 | 1 | 15 | — |
| storage | 11 | 0 | 0 | 11 | — |
| tasks | 2 | 0 | 1 | 1 | — |
| tiers | 5 | 2 | 0 | 3 | **P2** (same defect) |
| user_preferences | 4 | 0 | 3 | 1 | — (all 3 self-scoped) |
| webhooks | 11 | 0 | 0 | 11 | — |
| **TOTAL** | **117** | **5** | **18** | **94** | |

---

## Confirmed findings, ranked

### P2 — `GET /tiers`, `GET /tiers/{name}`, `GET /tiers/{tier_name}/rate-limits`, `GET /tiers/{tier_name}/rate-limits/{id}` are anonymous

`tiers/router.py:42,53` and `rate_limits/router.py:43,55`. Every **mutating**
route in these two routers carries `dependencies=[Depends(get_current_superuser)]`;
the four **reads** carry nothing — not `RequestContextDep`, not
`get_current_user`. `modules/router.py` is mounted with no `dependencies=`, and
the middleware stack in `core/guard/ordering.py` is CORS/GZip/QueryProfiler/
IPFilter/BodyLimit/SecurityHeaders/RateLimit/Tenant/AttackDetection/RequestID
— **no global authentication middleware exists**. So an anonymous caller gets
these rows.

Is that a leak? `Tier` (`tiers/models.py:41`) has **no `tenant_id` column** —
it is a global catalog (`FREE`/`BASIC`/`PRO`/`ENTERPRISE`) shared by every
tenant, and `RateLimit` hangs off it via `tier_id`. The data read is the rate
policy each tenant's own users are already subject to. So this is not a
cross-tenant disclosure.

It is still a defect, and the shape of the defect is the *asymmetry*: the
superuser gate on write and total absence on read means the read side is
reachable by anyone who can reach the internet, and nothing in the router
says so. `read_tiers`/`read_tier`/`read_rate_limits`/`read_rate_limit` take only
`request: Request` — they cannot even resolve the caller's tenant. Nothing in
the suite pins the auth state either way:
`tests/integration/test_frontend_compatibility.py:274` asserts only
`status_code != 404`.

**Precedent, and it raises the priority.** The same defect class was already
found and fixed in this codebase for the billing catalog — Run-3 finding
`billing.catalog_reads_scope_from_unauthenticated_header`, pinned by
`tests/integration/modules/billing/test_legacy_catalog_auth.py`. That test
asserts legacy catalog GETs reject anonymous callers and that
`X-Tenant-ID` cannot substitute for authentication. The tiers/rate-limits
catalog reads are the remaining instance of a class the project has already
decided is a defect. Rated P2 rather than P3 on that basis: the information
disclosed is low-value, but the *policy* is established and these four routes
contradict it.

Fix: add `dependencies=[Depends(get_current_superuser)]` to the four reads and
extend the existing catalog-auth test to cover them. Documenting them as
deliberately-public would also be defensible — but then it must be written
down, and the superuser gate on their sibling writes must be revisited too,
since a public catalog with a superuser-only write path is the same asymmetry
from the other side.

### P3 — `POST /billing/webhook` has no authentication idiom (expected)

`billing/router.py:67`, the single UNAUTHENTICATED mutating route in the twelve
modules. **Not a finding.** Stripe is the caller and cannot present a user
token; authentication is the Stripe HMAC signature, verified in
`ingest_webhook` (`billing/webhook/ingest.py:71-93`): missing `Stripe-Signature`
→ `ValidationError`, missing secret → `WebhookNotConfiguredError`, bad signature
→ `WebhookVerificationError`. The tenant is then derived from the **verified**
event metadata (`derive_and_attach_tenant`), not from a header the caller
controls. Invalid signatures additionally meter per-IP toward an auto-ban.

Recorded because a detector must report it and a human must dismiss it — that
exchange is the audit, not the absence of the route from the table.

---

## Detector hits that were NOT findings (21)

Every flagged route was read. These are the reasons.

### Anonymous reads (4) — see P2 above
`tiers/router.py:42,53`; `rate_limits/router.py:43,55`. Real but low-severity.
**REMEDIATED as sj09.7** (2026-09-30): the four reads now take
`ctx: RequestContextDep`, matching the `pgo0.1` billing-catalog precedent.
`RequestContextDep` was used rather than the writes' `get_current_superuser`
gate on purpose — a superuser gate on a global catalog read would deny every
ordinary tenant member the tiers their own subscription is priced from.

### Self-scoped by ownership in the service, not by a role gate (14)

The dominant false-positive class, and the one this detector exists to make
visible *by being wrong about it once*. In all of these the missing thing is a
**permission slug on the router**, and the present thing is an **ownership
predicate on the query**. A role gate would be wrong here — there is no
admin-only resource; the resource belongs to the caller.

| route | why it is safe |
|---|---|
| `ai/router.py:64` `POST /sessions` | creates a session for `ctx.user_id`; no other resource touched |
| `ai/router.py:111` `PATCH /sessions/{id}` | `update_session(session_id, ctx.user_id)`; owner predicate is **in the WHERE** (SEC-07), then `require_resource_ownership` |
| `ai/router.py:122` `DELETE /sessions/{id}` | `delete_session(session_id, ctx.user_id)`, same shape |
| `ai/router.py:177` `PATCH /settings` | `update_settings(ctx.user_id)`; keyed `(user_id, tenant_id)` |
| `ai/router.py:221` `POST /sessions/{id}/chat` | `_prepare_turn` → `get_session(session_id, user_id)` first; cannot chat on another's session |
| `ai/router.py:247` `POST /sessions/{id}/chat/stream` | same `_prepare_turn` gate |
| `posts/router.py:45` `POST /by-user/{username}` | `_require_post_owner(username, ctx)` → 403 unless `ctx.user.username == username` |
| `posts/router.py:100` `PATCH /by-user/{username}/{id}` | `_require_post_owner`, then `update_post(id, values, ctx.user)` |
| `posts/router.py:120` `DELETE /by-user/{username}/{id}` | `_require_post_owner`, then `delete_post(id, ctx.user)` |
| `security/router.py:92` `DELETE /passkeys/{id}` | `delete_credential(user_id=ctx.user_id, credential_id=passkey_id)` — own credential only |
| `user_preferences/router.py:39` `PATCH /preferences` | `update_preferences(user_id=ctx.user_id)` |
| `user_preferences/router.py:57` `PATCH /image` | `upload_profile_image(user_id=ctx.user_id)` |
| `user_preferences/router.py:76` `DELETE /image` | `delete_profile_image(user_id=ctx.user_id)` |
| `tasks/router.py:21` `POST /task` | enqueues with `tenant_id=ctx.tenant_id`; deterministic job id embeds the tenant |

Note `posts`: the four-router set is self-consistent — the cross-user hard
delete (`DELETE /by-user/{username}/db/{id}`, `posts/router.py:133`) *does*
carry `get_current_superuser`, precisely because it acts on another user. That
is the correct axis for that route, which is a point in the router's favour.

### Self-scoped with an explicit ownership check in the handler (2)

`billing/router.py:311` `POST /subscriptions/{id}/cancel` and
`routers/checkouts.py:86` `POST /cancel`. Both resolve the subscription and
compare against the caller. `checkouts.py:124` checks **both** `user_id` and
`tenant_id` before cancelling, raising `ForbiddenError`. `billing/router.py`
delegates to `cancel_subscription_for_user(subscription_id, user_id=..., is_superuser=...)`.

### Self-scoped payment initiation (2)

`billing/router.py:329` `POST /checkout` and `routers/checkouts.py:32`
`POST ""`. Both bind the Stripe session to `user_id`/`user_email` of the
caller. A user can create a checkout session **for themselves**; that is the
intended flow, and there is no role axis on "buy your own subscription". The
security-relevant controls (ownership at cancel, `MANAGE_BILLING` on
`POST /portal` and `PATCH /settings`) are all present where they belong.

---

## Detector hits vs. reality

| | count |
|---|---:|
| Raw detector hits | 23 |
| — UNAUTHENTICATED | 5 |
| — PARTIAL | 18 |
| Manually confirmed as real findings | 2 |
| Confirmed false positives | 21 |

The gap is made of: 4 anonymous catalog reads (real, P3), 1 signature-auth
webhook (correct as-is), and 18 ownership-scoped mutations where a permission
slug would be the wrong gate.

**The useful number is the PARTIAL class.** A scan that only asked "is this
route authenticated?" would have reported **1** problem (the webhook). All 18
PARTIAL routes are authenticated and 17 of them are correctly scoped — which is
the P1-1 shape fully closed. The one prior privilege-escalation defect in
`users/router.py` came from exactly this class, so it needed saying: in these
twelve modules it does not recur.

---

## What the detector cannot see (stated, not hidden)

- Whether an ownership predicate in a service is correct — it only reports that
  one *may* exist. Every one was read.
- Whether a permission slug on the gate is the *right* permission. The detector
  records the slug; it does not judge the axis.
- Router-level `dependencies=` on a **parent** mount. Every target router
  was checked: none declares `APIRouter(dependencies=[...])`, and
  `api/v1/core.py` / `api/v1/billing.py` mount them all without
  `dependencies=`. `--all-modules` widens the scan; the same check is manual
  for mounts.

## Re-running

The script is the detector of record. If a future route lands with a wrapped
auth dependency the resolver does not understand, it will read as
UNAUTHENTICATED — the same way the Stripe webhook does. That direction of error
is deliberate: it costs a human thirty seconds, and the alternative costs a
false "this is secure" on a payment endpoint.

Falsifiability is proven by `--selftest`, which plants an unauthenticated
`DELETE` in a synthetic router and asserts the detector flags it and nothing
else, alongside four positive controls covering all four idioms in both
positions plus the `RateLimitedUser` wrapper-alias hop.
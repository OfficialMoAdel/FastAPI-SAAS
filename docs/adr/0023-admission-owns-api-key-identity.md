# ADR-0023: Admission owns API-key identity; the response path consumes it

**Status:** ACCEPTED — 2026-10-17
**Date:** 2026-10-17
**Tracked by bead:** `FastAPI_saas-hfps` — "Resolve API-key identity once per request so admission meters the key owner's tier budget, not the source IP"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
**Depends on:** `FastAPI_saas-g4my` (the named per-source-IP API-key budget this ADR refines), `FastAPI_saas-u69m` (the deferral this ADR closes)
**Measured by:** `perf-results/RESULT-20261017-m7/m7_apikey_endpoint_baseline.json` (the baseline), `FastAPI_saas-oxx2` (the Phase-0 arm this ADR re-measures)

## Decision

**API-key identity is resolved once per request, by admission, and stored on
`request.state`. The authentication path consumes that resolution instead of
re-deriving it.**

Three rules, each mechanical:

1. **`src/app/core/security/api_key_identity.py` owns the API-key cache-key
   format and what a cached payload means.** `API_KEY_CACHE_PREFIX`,
   `API_KEY_CACHE_TTL` and `api_key_cache_key(digest)` move there;
   `api_keys/service.py` re-exports the two constants (its existing
   `event_bus = event_bus` pattern) so no importer changes. The module also owns
   the grant rules — *is this cached payload still a grant?* — in one function,
   `read_cached_grant`, which returns an identity or a refusal reason
   (`revoked` / `expired` / `not_yet_valid`). `APIKeyService.validate_api_key`
   calls it, so the rules have exactly one implementation.
2. **Admission resolves it, once, and binds it.** `RequestAdmissionService`
   reads the presented key, calls `resolve_api_key_identity`, and binds the
   result through `src/app/core/middleware_state.py`. When the resolution
   carries the owner's **tier**, admission meters the request on the owner's
   own bucket — `build_rate_limit_key(path, user_id=owner, tenant_id=...)`, the
   *same* bucket that owner's Bearer session spends. When it does not (cold key,
   or a cache entry written before this ADR), admission keeps g4my's
   `API_KEY_LIMIT` per source IP. **Never** a half-resolved state: no tier means
   no owner bucket, because `resolve_rate_limits` would otherwise fall to
   `DEFAULT_AUTHENTICATED_LIMIT = (50, 3600)` — a 20x *tightening* presented as
   a feature.
3. **The response path consumes it.** `get_user_by_api_key`
   (`src/app/api/deps/auth.py:534`) passes the bound identity to
   `validate_api_key(..., identity=...)`, which skips the cache probe and keeps
   everything else — the tenant-liveness probe, the `not_yet_valid` refusal, and
   the authoritative DB fallback. **No security check is skipped; one Redis GET
   is moved, not added.**

## Context

Admission runs before routing and before any auth dependency
(`src/app/core/guard/ordering.py::GUARD_ORDER` places RateLimit ahead of
Tenant), so at the moment admission decides a budget the presented key has not
been validated and no dependency has told anybody who owns it. g4my closed the
hole that produced — an API key was being metered as an anonymous browser,
10 requests/hour against a Pro account's 1000 — by giving keyed traffic its own
named budget, `settings.rate_limit.API_KEY_LIMIT = (1000, 3600)`, keyed by
**source IP** because a presented-but-unverified key must not be able to mint
buckets.

That is correct as far as it goes and it is what M-7 measured. It is not the
end state, and the remaining gap is real and specific:

- **Two integrations behind one NAT share one bucket.** oxx2 had to rotate
  sixteen `X-Forwarded-For` addresses to stay inside the budget; a customer
  cannot do that.
- **The owner's tier is ignored.** A key belonging to an `enterprise` account
  (10000/3600) and a key belonging to a `free` account (10/3600) get the same
  1000/3600, which is simultaneously too generous for the first and too harsh
  for the second.

The identity needed to fix this already exists and is already paid for:
`APIKeyService.validate_api_key` probes a Redis cache entry
(`api_key:<hmac>`, TTL 300 s) that **every** API-key request already reads, and
the response path already loads the owner from the database. So the information
is on the request; it is simply resolved on the wrong side of admission.

## Why the bead's own sentence is not implementable as written

`FastAPI_saas-hfps` asked to "resolve API-key identity once per request on the
cache `/users/me` already populates and have admission **consume** it". That
ordering cannot be built: `/users/me` runs *after* admission, so a cache entry
the response path populates is not readable by admission until the next request —
which is precisely the warm case admission needs to be cheap. Resolving once
means **admission** does the read. This ADR therefore inverts the bead's
sentence, states the inversion, and holds every acceptance criterion unchanged.

## Rejected alternatives

**Admission calls `APIKeyService.validate_api_key`.** Rejected on arrival, for
three independent reasons: (a) `core/` importing `modules/` inverts the
dependency direction the module rules depend on; (b) it makes the rate limiter's
cost depend on a module service, so a cache miss becomes a `SELECT … JOIN
tenants` on the hot path before any limit has been applied; (c) it duplicates
validation — the resolver in this ADR reads the *same* cache the module reads,
so the module keeps sole ownership of minting, revoking and rotation.

**Reorder the guard so admission runs after authentication.** Rejected, and the
reason is `u69m`'s: `_check_anonymous_aggregate`
(`ANONYMOUS_IP_AGGREGATE_LIMIT = (300, 3600)`) is the ceiling that stops a
single hostile address from spending a tier's budget before the tier is even
consulted. If admission moved after auth, an API-key flood would consume owner
tier capacity *before* the anonymous ceiling ever ran. A ceiling that can be
outrun by the traffic it exists to bound is not a ceiling.

**Meter on the source IP but with the owner's tier limit.** Rejected: it mixes
two identity axes — the bucket says "this IP", the limit says "this account" —
so revoking or downgrading an account leaves a bucket nobody can explain, and
two owners behind one address still share a counter.

**Look the owner up in the rate-limit table on every request.** Rejected: it is
a Redis round trip on the hot path to re-derive something the API-key cache
entry already has (once that entry carries the tier).

## The two traps this ADR exists to close

1. **Auto-ban regression.** `api_key_metered` is currently
   `ctx.api_key and ctx.token is None and ctx.user_id is None`
   (`request_admission.py:146`). The moment a warm resolution sets
   `ctx.user_id`, that expression goes false, an API-key violation starts
   earning `_handle_violation`, and a shared NAT behind one integration's
   misbehaviour gets an `AUTO_BAN_DURATION` outage for every caller behind it —
   the exact outcome g4my's oracle forbids. The flag must mean "this request
   presented an API-key credential", independent of whether it resolved.
2. **Silent tightening.** An identity without a tier must not be metered
   through `resolve_rate_limits`' anonymous-tier fallback (50/3600). Tier
   present ⇒ owner bucket. Tier absent ⇒ g4my's 1000/3600. There is no third
   outcome.

Both are pinned by the oracle
(`tests/unit/core/security/test_api_key_identity_admission.py`), not by review.

## Consequences

- **A resolved API-key request and that owner's Bearer session now share one
  bucket per path.** That is the intent, and it is also a behaviour change worth
  stating plainly: an integration and a human on the same account can now
  throttle each other. The alternative — separate buckets — is what the source
  IP was standing in for.
- **Cache entries written before this ADR carry no tier** and are therefore
  metered on `API_KEY_LIMIT` until they expire — at most one
  `API_KEY_CACHE_TTL` (300 s), self-healing, and never a tightening.
- **A cold key costs two extra Redis GETs** (admission probes both digest
  candidates, the response path then probes them again before its DB fallback):
  bounded to once per key per 300 s, and dominated by the DB read it precedes.
  Warm requests — the case the campaign and every real integration live in —
  cost exactly what they cost today.
- **The owner's tier is learned on the cold path** with one extra
  `LEFT JOIN` on the existing query (no new round trip), and is invalidated
  within one TTL after a tier change. That is the same staleness the key cache
  already has, not a new class of it.
- **The Phase-0 api-key arm is re-measured** (`FastAPI_saas-oxx2`'s artefacts).
  A change to admission that ships without a measurement is exactly what this
  programme exists to prevent.

## What is explicitly NOT being traded away

`API_KEY_LIMIT` keeps its value; the unverified bucket keeps its source-IP
keying; `GUARD_ORDER` is untouched; the anonymous aggregate ceiling keeps
bounding unauthenticated traffic; an API-key violation still does not escalate to
auto-ban; and no number in this ADR is asserted without a run behind it. g4my's
behaviour is preserved by construction and pinned by its oracle, which must stay
green.

## Re-open conditions

Reopen if the API-key cache ever stops carrying the owner's tier, because the
whole decision rests on that entry and `DEFAULT_AUTHENTICATED_LIMIT` would
become the silent floor. Reopen if `cache_client` ever becomes a multi-hop or
pipeline-only seam, because "one Redis GET, moved not added" stops being
true — and criterion 3 of `FastAPI_saas-hfps` is a measured claim, not a
promise. Reopen if admission ever moves behind the auth deps, which invalidates
the ordering argument above entirely.

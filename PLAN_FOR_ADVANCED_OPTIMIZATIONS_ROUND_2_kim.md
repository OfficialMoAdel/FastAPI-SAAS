# Plan for Advanced Optimizations — Round 2

> Investigation report on gross inefficiencies in the FastAPI SaaS boilerplate, with provably
> isomorphic (behavior-identical) optimization opportunities, ranked by (Impact × Confidence) / Effort.
> Date: 2026-09-10 · Scope: entire repo (349 `.py` files, 21 modules) · Status: report only, no code edited.

---

## 0. Executive Summary

After a full read of `AGENTS.md` + `README.md`, a personal audit of the request hot path
(`src/app/main.py`, all 9 middlewares, `core/db/database.py`, `core/db/rls.py`,
`core/security/jwt_utils.py`, `core/cache/client.py`, `core/pagination.py`, `core/json_utils.py`),
and three parallel deep sweeps over **all 349 source files** (`modules/**`, `core/**`,
`api/**` + `middleware/**`), the headline conclusion is:

> **This codebase has already undergone multiple aggressive performance-hardening rounds**
> (visible in the "P1-/R4-/H-3/P8/T9"-style fix annotations throughout). Pure-ASGI middleware,
> single-flight caches, fused Lua Redis ops, composite auth prefetch, bitmask RBAC, orjson
> everywhere, keyset pagination, capped OFFSET, bounded regex caches — all already done.
> **There is no remaining O(n²)-class gross inefficiency on the main request path.**
> What remains: one leftover `BaseHTTPMiddleware`, one per-DB-op global lock, one real N+1
> (webhook path, not request path), a few serialized Redis RTT pairs, and a set of per-request
> micro-allocations that run on 100% of traffic.

---

## A) Baseline

| Metric | Value | Command |
|---|---|---|
| Unit suite | **3641 passed / 11 skipped / 0 failed in 528.7s** | `uv run pytest tests/unit -q -p no:cacheprovider` |
| Source size | 349 `.py` files, 21 feature modules | `find src -name "*.py"` |

**Caveat (honest):** the investigation machine had no running Postgres/Redis, so live
p50/p95/p99 latency and throughput benchmarks could not be captured. Before implementing
anything below, stand up a local stack and baseline with e.g.
`uv run uvicorn src.app.main:app` + `wrk -t8 -c200 -d30s http://localhost:8000/api/v1/health`
and an authenticated-endpoint run, plus `py-spy record` / `memray` for CPU/alloc profiles.
The opportunity rankings are based on **static cost analysis + round-trip counting**, which is
reliable for I/O-bound findings (each eliminated Redis/DB RTT ≈ −0.3–1.5 ms on localhost, more
in production) but should be confirmed by profiling for the CPU-only items.

## B) Hotspot Identification (static substitutes for profiles)

Top per-request cost centers discovered:

1. **DB pool checkout → `restore_rls_context` adds 1 RTT per checkout** (`core/db/rls.py:58-108`) —
   *deliberate fail-closed invariant; documented; left alone.*
2. **Every CRUD op wraps `db_circuit_guard()`** → `record_success()` takes a **process-global
   `asyncio.Lock`** per op (`core/utils/circuit_breaker.py:98`).
3. **`AttackDetectionMiddleware` is the only remaining `BaseHTTPMiddleware`** on the hot path
   (`middleware/attack_detection.py:119`).
4. **Authenticated requests pay 2 sequential Redis RTTs** in `verify_token`
   (`core/security/jwt_service.py:360-436`) when the composite prefetch isn't in play.
5. **Per-request string/regex work** in exempt-path checks, bad-bot regex validation,
   URL re-parsing (see R7/R8).

---

## C/D) Findings with Isomorphism Proofs — Opportunity Matrix

Ranked by **(Impact × Confidence) / Effort**. All diffs are single-lever, minimal, with
rollback = revert the one function/class.

### 🔴 R1 — `AttackDetectionMiddleware` → native ASGI (Impact H, Conf H, Effort M)

`middleware/attack_detection.py:119,423`. The only middleware still on `BaseHTTPMiddleware`,
which spawns an anyio task-group per request and pumps **every response byte** through a
memory-object-stream queue, then re-serializes into a fresh `Response`/`_StreamingResponse`.
`TenantMiddleware` (`tenant.py` class docstring, "P8") already documents and executes exactly
this conversion — the pattern to copy exists in-repo. The middleware never inspects the
response; it only re-injects a buffered request body.

**Proof:** `dispatch` returns `call_next(request)` unmodified ⇒ relaying `send` verbatim is
byte-identical; the wrapped-`receive` body reinjection is a 1:1 map of the existing closure.
**Guardrail:** SSE/streaming endpoint behavior test + existing `tests/e2e` attack-detection contracts.

### 🔴 R2 — Circuit-breaker fast path (Impact H under load, Conf H, Effort S)

`core/utils/circuit_breaker.py:98` + `core/db/tenant_crud.py` (every CRUD op).
`record_success()` unconditionally acquires a **single global asyncio.Lock** and does 2
function-local imports per DB operation. Under concurrency this serializes *all* DB ops
process-wide on one lock.

**Fix:** early return when `state is CLOSED and _failure_count == 0 and not _probe_in_progress`;
hoist the two imports to module top.

**Proof (verified against source):** in that state the locked body executes only
`_probe_in_progress = False` (already False) and no state transition; racing failure paths
still take the locked branch unchanged.

### 🟠 R3 — N+1 in Stripe refund webhook handler (Impact H on webhook path, Conf H, Effort S)

`modules/billing/webhook/handlers/refunds.py:122-141` — a loop of per-refund
`crud_billing_records.exists()` SELECTs (1 DB RTT per refund row) followed by per-refund
INSERTs. **This is the only true N+1 found in the entire codebase.**

**Fix:** one `SELECT stripe_refund_id WHERE stripe_refund_id IN (:ids)` into a `set`, then
loop membership checks.

**Proof:** per-id `exists` and set-membership over the identical id list in the same
transaction return identical booleans; creation rows and commit boundary unchanged.

### 🟠 R4 — Fuse the two sequential Redis RTTs in `verify_token` (Impact M-H, Conf H, Effort S)

`core/security/jwt_service.py:360-436`. Blacklist Lua EVALSHA awaited, *then* a separate
`GET user:{sub}:token_version` — no data dependency between them. The repo already has the
template: `composite_auth_prefetch` (`core/utils/lua_manager.py`, `auth_prefetch.py`).

**Fix A:** add `token_version` as KEYS[5] to the composite script and return it as an extra
result element. **Fix B (minimal):** `asyncio.create_task` the blacklist check and `await`
after issuing the GET.

**Proof:** two independent reads of disjoint keys; Lua execution is atomic (strictly
stronger); neither write path (negative-cache SETEX, version bumps) is perturbed.

### 🟠 R5 — RBAC bitmask cache-miss stampede (Impact M on cold cache, Conf H, Effort M)

`core/rbac/dependencies.py:81-129`. No singleflight on the 300s `perms:bitmask:*` TTL expiry
→ every concurrent request for the user runs the 4-table join
(`UserRole → Role → RolePermission → Permission`). The identical problem is already solved in
this codebase for `token_version` via `_in_flight_token_version` (`jwt_service.py:223-264`) —
copy that pattern.

**Proof:** all waiters receive the same computed mask; winner-only `setex` writes the same
(key, value, TTL).

### 🟠 R6 — Jinja recompilation per email (Impact M on notification-heavy load, Conf H, Effort S)

`modules/notifications/service.py:158` — `jinja_env.from_string(template_content)` compiles
(lex→parse→codegen) **per email**; the source itself is already cached (1h Redis TTL).

**Fix:** memoize compiled `Template` by `sha256(content)` in a small bounded dict (the
`_FN_REGEX_CACHE` pattern from `webhooks/service.py` applies).

**Proof:** `from_string` is a pure function of `(env, source)`; content-addressed keying ⇒
zero staleness, identical render output byte-for-byte.

### 🟡 R7 — Exempt-path prefix checks (Impact M on 100% of requests, Conf H, Effort S)

`core/middleware_exempt.py:59-80` — f-string + `startswith` loop over ~12+ prefixes, called
from 3 middlewares per request (~30+ string allocs/request on the miss path).

**Fix:** module-level `tuple(p + "/" for p in PREFIXES)` + single `path.startswith(tuple)`
(C-speed, OR-semantics).

**Proof:** `str.startswith(tuple)` ≡ the loop's early-True boolean semantics; frozenset
iteration order is irrelevant for a pure-OR predicate.

### 🟡 R8 — Attack-scan pre-processing waste (Impact M, Conf H, Effort S)

`attack_detection.py`:
- (a) `str(request.query_params)` parse→re-encode round trip → scan `scope["query_string"]`
  bytes directly (the patterns target *encoded* forms like `%2e%2e%2f` anyway);
- (b) 13 × `request.headers.get(name)` — Starlette linearly scans+lowercases all headers per
  call → one pass over `scope["headers"]` collecting the 13 names into a dict;
- (c) `rate_limit.py:104` `urlparse(str(request.url))` per request → `scope["path"]` for the
  two fixed ASCII comparison targets (`/api/v1/auth/refresh`, `/api/v1/account/refresh`).

**Proofs:** `str(QueryParams(raw))` reproduces the raw query bytes; single-pass first-match
collection returns identical header values to `Headers.get`; `urlparse(str(url)).path == scope["path"]`
for targets containing no percent-encodable characters.

### 🟡 R9 — DLQ admin ops: O(N) RTTs + full materialization (Impact M on admin ops, Conf H, Effort M)

`core/events/bus.py:647-682` (`retry_dlq_entry`, `clear_dlq`) — `LRANGE 0 -1` of up to 10k
JSON blobs, then **one `LREM` RTT per entry**.

**Fix:** small server-side Lua script (scan + LREM, return removed payloads) registered in the
existing `LuaScriptManager.BUILTIN_SCRIPTS`.

**Proof:** identical result sets/return values; Lua is strictly *more* atomic than the current
sequential loop.

### 🟡 R10 — Cache-decorator double Pydantic pass on writes (Impact M on cached list endpoints, Conf H, Effort S)

`core/utils/cache/decorator.py:~360-390` — per item:
`response_model.model_validate(item).model_dump(mode="json", by_alias=True)`, then orjson
re-walks the resulting dicts (validate → dict → bytes, two full Pydantic passes + an orjson
walk per list element).

**Fix:** memoize `TypeAdapter(list[Model])` per model class and single-pass
`dump_python(result, mode="json", by_alias=True)`.

**Proof:** same model, same mode, same alias settings ⇒ identical JSON structure; the
intermediate per-item model objects carry no state into the output.

### 🟢 Low tier (micro, all isomorphic; batch as one "hot-path hygiene" PR)

- `ip_filter.py:38-48`: memoize the bad-bot **source tuple** instead of re-escaping + joining
  the whole pattern string per request (compare tuples, compile on change — same predicate).
- `gzip_filter.py:101`: replace per-request header `dict()` build with a **last-match** loop
  for `b"accept"` (provably identical to `dict` semantics).
- `tenant.py:388`: `_get_tenant_from_cache_data` is `async` with zero awaits — make it sync
  (3 call sites drop `await`).
- `security_headers.py:79-105`: 7 `setdefault` linear scans per response → one membership set
  + single header-list build.
- `token_blacklist_cache.py:114-125`: collapse double `get_script` lookup + exception frame
  into direct `execute()` with `ScriptNotFoundError` as the fallback signal (keep the shim
  for tests that patch it).
- `dashboard/service.py:61`: cache `MIN(created_at)` per tenant (monotonic; invalidate on
  tenant user creation).
- `billing/service.py:565`, `users/service.py:443,469,516,562`: drop
  `model_validate(...).model_dump()` before the router's `response_model` re-validation
  (model→dict→model triple round trip; pass the model through).

### ⚪ Flagged — NOT provably isomorphic (require a product decision; excluded from any automatic patch)

- **Double rate limiting:** middleware GCRA *and* dependency-level `rate_limiter_dependency`
  (`api/deps/core.py:21-47`) use different key formats (`rate_limit:user:…` vs `{user}:{path}`) —
  coalescing changes accounting semantics.
- **`lazy="selectin"` fan-out** on billing/webhooks/api_keys/notifications models adds 1–3
  `SELECT IN` per list query; per-endpoint `.options(noload(...))` is safe, but only after
  verifying each endpoint never touches those attributes.
- **RLS checkout `set_config` RTT** (`db/rls.py`) — fail-closed invariant, documented; any
  skip breaks the security proof.
- **API-key auth's two serial Redis GETs** (`api_keys/service.py:327-353`) — merging requires
  either Lua-fetched tenant id or complete eviction-on-suspend; an eviction-reliability fix
  first is the correct order of operations.
- **`AuthenticatedUser(**user)` Pydantic revalidation per request** (`api/deps/auth.py:73-77`) —
  `model_construct` is faster but changes behavior on malformed data.

---

## E–G) Execution Plan & Guardrails

1. **Order:** R8+R7 (hygiene) → R2 → R1 → R4 → R3 → R5 → R6 → R9 → R10. One lever per commit.
2. **Equivalence oracle:** the existing suite (3641 tests, incl. e2e security contracts) is the
   golden-output oracle; add metamorphic tests for R1 (attack-detection verdicts on a corpus of
   attack/benign payloads before/after conversion), and property-based tests for R7 (random
   paths × prefix sets, loop vs tuple).
3. **Benchmarks:** add `tests/bench/` with pytest-benchmark scenarios for:
   - authenticated-request RTT count (mock Redis counting commands — regression guardrail
     against re-introducing extra RTTs),
   - middleware overhead (ASGI harness, synthetic scope),
   - cache-decorator serialization throughput.
4. **Monitoring hooks:** the existing pool metrics + query profiler (`QUERY_PROFILER_*`) plus
   Prometheus counters make RTT-count regressions observable in staging.
5. **Rollback:** every item is a single-function/single-class diff — `git revert` per commit;
   no schema, config, or protocol changes involved.

---

## Expected Impact

Roughly **−1 to −2 Redis RTTs per authenticated request** (R2's lock removal also cuts
event-loop contention measurably under concurrency), **removal of the last
response-byte-copying middleware** (R1 — the largest single latency win, especially for
streaming/SSE), and elimination of the codebase's one true N+1 (R3). Estimated p95 impact:
low-single-digit ms on simple endpoints, more under concurrency — meaningful but incremental,
because the previous optimization rounds already captured the gross wins.

Tracked in Beads as a P2 task: "Perf: implement isomorphic optimization batch R1-R10".

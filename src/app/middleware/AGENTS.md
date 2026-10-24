# Middleware Layer - Agent Guidance

## Package Identity

Starlette middleware stack for tenant isolation, rate limiting, security hardening, and request observability. All middleware is registered by iterating `core/guard/ordering.py::GUARD_ORDER` in `src/app/main.py` — nowhere else.

## Module Structure

```
src/app/middleware/
├── __init__.py            # Exports all middleware
├── attack_detection.py    # SQLi/XSS/path-traversal detection (log/block/ban modes)
├── body_limit.py          # RequestBodyLimitMiddleware (MAX_BODY_SIZE)
├── gzip_filter.py         # GZip with content-type filter (exempts SSE/binary)
├── ip_filter.py           # IP whitelist/blacklist (Redis-backed, exact-key matching)
├── query_profiler.py      # Dev-mode DB query profiling (disabled in production)
├── rate_limit.py          # GCRA user/tier-based rate limiting
├── request_id.py          # X-Request-ID propagation (innermost)
├── security_headers.py    # XSS-prevention headers
└── tenant.py              # Tenant context extraction (header → validation → RLS)
```

## Setup & Run

```bash
# No separate setup - middleware registered in main.py
uv run pytest tests/unit/middleware/ -v
```

## Patterns & Conventions

### DO: Write native ASGI middleware (scope/receive/send)

The hot path here is native ASGI. `BaseHTTPMiddleware.dispatch` pays an
anyio task-group + memory-stream hop that double-buffers request bodies, so
it is measurably more expensive per request for no benefit on these
guards — this is the same call recorded in `src/app/AGENTS.md`.

```python
# Template matching RateLimitMiddleware / BodyLimitMiddleware:
from starlette.types import ASGIApp, Receive, Scope, Send

class MyMiddleware:
    """Native ASGI middleware."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # ... guard logic; reject via core.errors.middleware_error_response ...
        await self.app(scope, receive, send)
```

### DON'T: Reach for `BaseHTTPMiddleware`

Every middleware in this package is plain ASGI. Two of them record why:
`attack_detection.py` notes "P1-6: converted from BaseHTTPMiddleware" and
`tenant.py` notes "I15: no longer subclasses BaseHTTPMiddleware". If a new
middleware seems to need `dispatch`, it is the one that has to justify the
double-buffering — not the other way around.

Registration order is owned by `src/app/core/guard/ordering.py::GUARD_ORDER`
(pinned by `tests/unit/test_middleware_order.py`) — never add middleware by
editing call sites.

### DON'T: Skip tenant context cleanup

```python
from src.app.core.tenancy.context import clear_tenant_context

finally:
    clear_tenant_context()
```

### DO: Use the central exempt-path helper

```python
# src/app/core/middleware_exempt.py — single source of truth for exempt paths
from src.app.core.middleware_exempt import is_path_exempt
```

## Registration Order (from `core/guard/ordering.py` — single wiring point)

Middleware is LIFO — last added runs first on request. Pinned by
`tests/unit/test_middleware_order.py` (R1-16: the order is a security
contract — a reorder fails CI):

```
Request flow:
  CORS → GZip → QueryProfiler → IPFilter → BodyLimit → SecurityHeaders
    → RateLimit → Tenant → AttackDetection → RequestID → Handler
```

Key decisions (documented inline in main.py):
- **RequestID**: first added = innermost, closest to handler for accurate tracing
- **RateLimit** runs before Tenant so a cheap 429 never pays the tenant lookup (P1-15), and before AttackDetection's expensive body scan so 429s reject first (R4)
- **AttackDetection** runs after BodyLimit, so oversized payloads are rejected by 413 before the scan buffers them
- **IPFilter** runs early and sets `ip_whitelisted` state consumed by RateLimit
- **GZip** compresses all responses including errors
- **CORS** outermost (last entry in `core/guard/ordering.py`) so guard short-circuits — IPFilter 403, RateLimit 429, BodyLimit 413 — still carry `Access-Control-Allow-Origin`

## Touch Points / Key Files

- Attack detection: `attack_detection.py` — config `ATTACK_DETECTION_ENABLED`, `ATTACK_DETECTION_MODE` (log/block/ban), `ATTACK_DETECTION_LOG_FULL_REQUEST`
- IP filter: `ip_filter.py` — Redis-backed, exact-key IP allowlist/blocklist (no CIDR ranges), auto-ban integration
- Rate limit: `rate_limit.py` — GCRA (Redis), per-user/tier limits
- Query profiler: `query_profiler.py` — `QUERY_PROFILER_ENABLED`, `QUERY_PROFILER_SLOW_THRESHOLD_MS`; adds `X-Query-Count`/`X-Query-Time` headers
- Tenant: `tenant.py` — header validation → tenant context → RLS session variables
- Exempt paths: `src/app/core/middleware_exempt.py`

## JIT Index Hints

```bash
# Find middleware classes
rg -n "class.*Middleware" src/app/middleware/

# Find middleware registration
rg -n "add_middleware" src/app/main.py

# Find tenant context usage
rg -n "tenant_context|clear_tenant_context" src/app/

# Find rate limit checks
rg -n "rate_limit" src/app/middleware/
```

## The body-scan method gate

`attack_detection.py` scans the **path, the query string and the security-relevant headers for
EVERY method**, and the **body** only for `POST`/`PUT`/`PATCH` (`_BODY_METHODS`). The gate
is at `attack_detection.py:482-488`:

```python
        if method not in _BODY_METHODS:
            if logger.is_enabled_for(logging.DEBUG):
                logger.debug(
                    "attack_detection_skipped_body_scan_for_method",
                    method=method,
                    path=path,
                )
```

Two facts about that guard that are easy to get wrong:

- **The predicate is real.** Since A-3 (`FastAPI_saas-49yx`, landed as `7e0e6a8f`) the
  bound logger is `LevelAwareBoundLogger`, whose `is_enabled_for` delegates to the wrapped
  stdlib logger. So it is `False` at INFO and `True` at DEBUG. Before A-3 it was
  structurally always `True` — the guard gated nothing.
- **The skip removes no attack coverage.** A bodyless GET still has its path, query string
  and headers scanned (a `user-agent` carrying a command in command position is still
  detected, with `location=header:user-agent`).

### The trap: structlog's ambient default is always True

`setup_logging()` is called from the application's **lifespan**, so it does not run in a
test that drives the app over `ASGITransport` (the e2e `client` fixture says so in its own
comment). Without it, structlog stays at its ambient default
(`BoundLoggerFilteringAtNotset`), whose predicate is **structurally always `True`**: the
guard never closes at any level and every assertion about it is vacuous. A test that
exercises this guard must install the configuration itself and assert that the bound logger
is a `LevelAwareBoundLogger`.

Two oracles cover it, at different levels:

| what | where |
|---|---|
| the real log RECORDS, over the real ASGI stack, at INFO and at DEBUG | `tests/e2e/feature_validation/test_s8_attack_scan_gate_e2e.py` (bead `FastAPI_saas-xfpt`; recorded artefact `perf-results/RESULT-20261019-xfpt/`) |
| COUNTS of guard evaluations and `logger.debug` invocations per level | `src/benchmarks/s8_logger_call_counter.py` (bead `FastAPI_saas-uo44`; recorded artefact `perf-results/RESULT-20261019-uo44/`) |
| the predicate at DEBUG and at INFO, at the unit level | `tests/unit/middleware/test_attack_scan_method_gate.py` |

## The prefilter: a necessary literal, and the cmd_inj CONJUNCTION

The scan has a cheap first gate: `_attack_prefilter_hit(content)`. If it misses, the
17-group regex cannot match, so the expensive `finditer` is skipped. Each literal in
`_PREFILTER_LITERALS_BYTES` is **necessary** (not sufficient) for its group.

**The `cmd_inj` group's entry is a CONJUNCTION, and the gate now checks it as one.**
`cmd_inj` matches `(?:;|\|\||&&)\s*(?:cat|ls|id|whoami|ping|curl|wget)\b` — a separator
AND a command word. A separator on its own is necessary but not sufficient, and a browser
`user-agent` carries `;` on nearly every request (`Linux; Android 14; …`), so the bare
separator used to send the full regex over every ordinary header.

- `_PREFILTER_LITERALS_BYTES` is **unchanged at 15 literals**. The three separators stay
  in it — they are still NECESSARY for `cmd_inj`, and the global set is what the
  `LC-C2-OR` gate reads — but they no longer admit content on their own.
- `_PREFILTER_SINGLE_LITERALS` is the **effective** set the automaton and the `bytes in`
  fallback iterate: the 15 minus the three separators (12).
- `_CMD_INJ_GATE` is a second compiled regex built **from the production `cmd_inj`
  sub-pattern**, extracted from `COMBINED_ATTACK_RE.pattern` at import — so it cannot
  drift from the group it mirrors. It is a regex rather than a hand-rolled scan because
  the regex tries every POSITION: `|||cat` matches (the `||` at index 1), and a
  non-overlapping `bytes.find` would miss that occurrence, which is a missed verdict.
- **ROLLBACK is one line:** `_PREFILTER_SINGLE_LITERALS = _PREFILTER_LITERALS_BYTES`.

❌ DON'T: narrow the GLOBAL literal set to change one group's behaviour. Removing `;` from
the global set breaks `sql_comment` and `sql_semicomment` silently — they need `--`, and
only `--` is in the set. That is what `LC-C2-OR` (in
`scripts/perf-validation/isomorphism_validator.py`, which runs in CI) exists to catch.

❌ DON'T: record the benign `user-agent` as "Pixel has no `id`". It HAS one — `Android`
contains `id` (A-n-d-r-o-i-d) — and the reason it is benign is the **POSITION**: the `id`
is mid-word, and no command word follows any `;` at a word boundary. The case and that
reason are pinned in `tests/unit/middleware/test_attack_pruning_differential.py` and in
the validator's `C2_pixel_ua` gate.

The narrowing itself has an e2e differential oracle:
`tests/e2e/feature_validation/test_c2_prefilter_conjunction_e2e.py` (bead
`FastAPI_saas-mhj4`; recorded artefact
`perf-results/RESULT-20261020-mhj4/c2_prefilter_differential.json`). It runs the
whole seeded corpus through BOTH the reconstructed pre-C-2 prefilter and the
shipped one, and asserts **zero verdict differences**: the narrowing may admit
LESS, but it must never change a verdict. It also pins the `--` companion in
BOTH literal sets, and asserts the mobile `user-agent` is benign because of the
POSITION of `id` (`Android` contains it, `Pixel` does not).

## The body-scan offload (A-8): a CAPACITY lever, never a detection lever

`ATTACK_DETECTION_BRANCH_PRUNE_OFFLOAD` (default **False**) moves the request-BODY
pattern scan into a worker process. It is off by default because the process arm's
p95 was never measured on the Linux target — L-13's parity figures are for the
THREAD arm — so switching it on would be an unmeasured production change.

- `scan_content_for_attacks(content, location, log_full_request)` is module level and
  **RETURNS** its findings instead of appending to a list the caller owns. That is not
  a style choice: a worker process cannot mutate the parent's list, so a scan that
  crosses a process boundary must hand its result back.
- `_scan_in_worker` is module level so it is picklable **by reference**. A lambda or a
  closure would not survive the pool's serialiser — one of the three obstacles L-13
  recorded against the process arm.
- The pool is created **once**, in `AttackDetectionMiddleware.__init__`, and stays warm.
  It is never created per request.
- **The fail direction is fail-to-current WITH A CIRCUIT BREAKER** (ADR-0016): a
  pool-side error (broken pool, pickling failure, worker death, cancellation) falls
  back to the in-process scan on the event loop. No request is left unscanned and
  none is rejected because the pool failed. The fallback increments
  `r1_attack_scan_pool_failure_total` (`record_attack_scan_pool_failure()`), so a
  dead pool is visible during an incident instead of being a bare log line.
- More than 5 pool failures in any 60 s window opens the breaker: the offload is
  disabled, an ERROR is logged, and it stays off until the process restarts or an
  operator re-enables it.
- The prefilter decision is made in the **parent** (`_should_scan`), because the
  prune counter lives there and a child cannot increment it.
- The worker uses the **platform default** start method (fork on Linux). Forcing
  spawn/forkserver has a second cost: a spawned child re-imports the parent's
  `__main__`, which under a console-script launcher cannot even start. That is a
  start-up failure, and the fail direction covers it. No start-up cost figure is
  quoted for either start method: `[Unverified - Needs-Linux-measure]`.

❌ DON'T: treat the offload as a detection change. It finds exactly the same attacks
as the in-process scan; the oracle `tests/unit/middleware/test_a8_body_scan_offload.py`
asserts the two paths produce the same findings, that a pool failure falls back AND
is counted, and that the breaker opens and stops the offload being used.

### The other arm: the thread pool is best-effort, and it is the default

With the offload **off** (the default), `_scan_body` still has a second arm for
bodies above `_BODY_OFFLOAD_THRESHOLD`: the loop's default **thread** pool via
`run_in_executor(None, ...)`. Do not read that as event-loop protection.
ADR-0008 measured it as **best-effort, not a real offload** — `re` is pure
Python and does not release the GIL, so a worker thread holds the interpreter
lock for the whole match:

| body size | inline on loop | offloaded to a thread |
|---|---|---|
| 4 KB | 1.2× | 1.4× |
| 8 KB | 1.3× | 1.6× |
| 32 KB | 3.2× | 2.3× |

The **process** pool above is the arm that actually recovers responsiveness
(a separate interpreter, so no GIL contention). So on a default deployment
neither large-body arm protects the loop, and the correct statement is that
`_BODY_OFFLOAD_THRESHOLD` bounds *how much* regex runs inline — not that the
loop stays free.

## Common Gotchas

- **Order matters**: registration order = reverse execution order; see the flow diagram in `main.py` before inserting a new middleware
- **Tenant context**: must be cleared in `finally` blocks to prevent cross-request leakage
- **CORS**: configured from `settings.cors` in `core/guard/ordering.py::_cors_kwargs` (skipped when `CORS_ORIGINS` is empty), NOT in `create_application`
- **Rate limiting**: applied per-user/tier via Redis GCRA; health/auth paths are exempt
- **CORS preflight**: OPTIONS requests bypass most middleware

## Pre-PR Checks

```bash
uv run pytest tests/unit/middleware/ -v && uv run ruff check src/app/middleware/ && uv run mypy src/app/middleware/
```

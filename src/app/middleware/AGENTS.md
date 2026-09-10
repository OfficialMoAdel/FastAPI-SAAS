# Middleware Layer - Agent Guidance

## Package Identity

Starlette middleware stack for tenant isolation, rate limiting, security hardening, and request observability. All middleware is registered in `src/app/main.py` — nowhere else.

## Module Structure

```
src/app/middleware/
├── __init__.py            # Exports all middleware
├── attack_detection.py    # SQLi/XSS/path-traversal detection (log/block/ban modes)
├── body_limit.py          # RequestBodyLimitMiddleware (MAX_BODY_SIZE)
├── gzip_filter.py         # GZip with content-type filter (exempts SSE/binary)
├── ip_filter.py           # IP whitelist/blacklist (Redis-backed, CIDR support)
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

### DO: Use Starlette BaseHTTPMiddleware

```python
from starlette.middleware.base import BaseHTTPMiddleware

class MyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Pre-processing
        response = await call_next(request)
        # Post-processing
        return response
```

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

## Registration Order (from `src/app/main.py`)

Middleware is LIFO — last added runs first on request:

```
Request flow:
  GZip → QueryProfiler → IPFilter → BodyLimit → AttackDetection
    → SecurityHeaders → Tenant → RateLimit → RequestID → Handler
```

Key decisions (documented inline in main.py):
- **RequestID**: first added = innermost, closest to handler for accurate tracing
- **RateLimit** before expensive processing; **AttackDetection** after body limit so a cheap 429 rejects oversized payloads first
- **IPFilter** runs early and sets `ip_whitelisted` state consumed by RateLimit
- **GZip** last = outermost, compresses all responses including errors

## Touch Points / Key Files

- Attack detection: `attack_detection.py` — config `ATTACK_DETECTION_ENABLED`, `ATTACK_DETECTION_MODE` (log/block/ban), `ATTACK_DETECTION_LOG_FULL_REQUEST`
- IP filter: `ip_filter.py` — Redis-backed, CIDR + exact matches, auto-ban integration
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

## Common Gotchas

- **Order matters**: registration order = reverse execution order; see the flow diagram in `main.py` before inserting a new middleware
- **Tenant context**: must be cleared in `finally` blocks to prevent cross-request leakage
- **CORS**: applied in `core/setup.py::create_application` (from `settings.cors`), NOT in main.py
- **Rate limiting**: applied per-user/tier via Redis GCRA; health/auth paths are exempt
- **CORS preflight**: OPTIONS requests bypass most middleware

## Pre-PR Checks

```bash
uv run pytest tests/unit/middleware/ -v && uv run ruff check src/app/middleware/ && uv run mypy src/app/middleware/
```

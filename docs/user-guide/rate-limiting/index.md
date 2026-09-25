# Rate Limiting

The boilerplate includes a sophisticated rate limiting system built on Redis that protects your API from abuse while supporting user tiers with different access levels. This system provides flexible, scalable rate limiting for production applications.

## Overview

Rate limiting controls how many requests users can make within a specific time period. The boilerplate implements:

- **Redis-Based Storage**: Fast, distributed rate limiting using Redis
- **User Tier System**: Different limits for different user types  
- **Path-Specific Limits**: Granular control per API endpoint
- **Fallback Protection**: Default limits for unauthenticated users

## Quick Example

General path limits are enforced by the ASGI `RateLimitMiddleware` on
every request (tier-aware; no per-endpoint wiring needed). Endpoint-level
brute-force budgets go through the engine's security-budget verb:

```python
from src.app.core.security.rate_policy import check_security_budget

@router.post("/send")
async def send(body: SendRequest):
    # "auth"-style budget keyed on an identity (IP, user id, email hash);
    # (limit, period) come from settings.rate_limit.SECURITY_BUDGETS
    verdict = await check_security_budget("magic_link_send", email_identity)
    if verdict.is_limited:
        raise RateLimitException("Too many requests. Try again later.")
```

## Architecture

### One Engine

Endpoint brute-force budgets resolve through `core/security/rate_policy.py`
via `check_security_budget(budget, identifier)` — identity-keyed budgets for
sensitive endpoints (login, MFA, passkey, magic link, AI chat). One key
scheme, one algorithm selector (GCRA or fixed-window), one fail-open policy.
(`evaluate_rate_policy` is a deprecated general-purpose evaluator with no
production callers — tests only; it lacks the middleware's ban/blacklist
merge.) Tier path policy for general traffic lives in the same module
(`resolve_rate_limits` + `build_rate_limit_key`) and is composed by the ASGI
`RateLimitMiddleware` together with the ban/blacklist merge.

### How It Works

1. **Request Arrives**: `RateLimitMiddleware` (ASGI) applies tier policy,
   or a router dependency applies a security budget
2. **Limit Lookup**: tier path policy (L1 → Redis cache → DB → settings)
   or the named budget in settings
3. **Algorithm Check**: GCRA (default) or fixed window via one shared Redis
   round-trip
4. **Allow/Deny**: request proceeds or returns 429 Too Many Requests with
   `Retry-After` / `X-RateLimit-*` headers

An operator-declared IP bypass skips the counter, not the ban boundary. Its
verdict has no numeric quota fields and emits only `X-RateLimit-Bypassed: true`;
it does not fabricate `X-RateLimit-Limit`, `X-RateLimit-Remaining`, or
`X-RateLimit-Period` values.

## User Tier System

### Default Tiers

The system supports flexible user tiers with different access levels:

```python
# Example tier configuration
tiers = {
    "free": {
        "requests_per_minute": 10,
        "requests_per_hour": 100,
        "special_endpoints": {
            "/api/v1/ai/generate": {"limit": 2, "period": 3600},  # 2 per hour
            "/api/v1/exports": {"limit": 1, "period": 86400},     # 1 per day
        }
    },
    "premium": {
        "requests_per_minute": 60,
        "requests_per_hour": 1000,
        "special_endpoints": {
            "/api/v1/ai/generate": {"limit": 50, "period": 3600},
            "/api/v1/exports": {"limit": 10, "period": 86400},
        }
    },
    "enterprise": {
        "requests_per_minute": 300,
        "requests_per_hour": 10000,
        "special_endpoints": {
            "/api/v1/ai/generate": {"limit": 500, "period": 3600},
            "/api/v1/exports": {"limit": 100, "period": 86400},
        }
    }
}
```

### Rate Limit Database Structure

```python
# Rate limits are stored per tier and path
class RateLimit:
    id: int
    tier_id: int           # Links to user tier
    name: str             # Descriptive name
    path: str             # API path (sanitized)
    limit: int            # Number of requests allowed
    period: int           # Time period in seconds
```

## Implementation Details

### Automatic Rate Limiting (ASGI middleware)

Every non-exempt path is rate limited by `RateLimitMiddleware` — tier-aware
policy, ban coupling, and response headers without per-endpoint wiring.
Exempt paths (health probes, docs, webhook receivers) live in
`core/middleware_exempt.py`.

Do not add a general-purpose Depends limiter — it double-limits what the
middleware already covers (this was removed; see `api/deps/core.py` note).
For endpoint-specific brute-force budgets, extend
`settings.rate_limit.SECURITY_BUDGETS` and call `check_security_budget`.

### Security Budgets

```python
# settings.rate_limit.SECURITY_BUDGETS (core/config/services.py)
SECURITY_BUDGETS = {
    "auth": (5, 60),            # login/register/refresh/reset per IP
    "mfa_verify": (5, 60),     # MFA verification attempts
    "mfa_mutate": (5, 60),     # MFA state changes (separate budget, P1-5)
    "magic_link_send": (3, 60),# per hashed email (anti-enumeration)
    "passkey_login": (10, 60), # passkey login per IP
    "ai_chat": (60, 60),       # AI completions per user
}
```
### Redis-Based Counting

The engine delegates counting to `RateLimiter.check_rate_limit_algorithm`,
which picks GCRA (default, no boundary bursts) or fixed window per
`RATE_LIMIT_ALGORITHM`, and honors `RATE_LIMIT_FAIL_OPEN` on Redis outages.
Callers never hand-roll keys — `build_rate_limit_key` is the one scheme.

### Path Sanitization

API paths are sanitized for consistent Redis key generation:

```python
def sanitize_path(path: str) -> str:
    return path.strip("/").replace("/", "_")

# Examples:
# "/api/v1/users" → "api_v1_users"
# "/posts/{id}" → "posts_{id}"
```

## Configuration

### Environment Variables

```bash
# Rate Limiting Settings
DEFAULT_RATE_LIMIT_LIMIT=100      # Default requests per period
DEFAULT_RATE_LIMIT_PERIOD=3600    # Default period (1 hour)
```

### Creating User Tiers

```python
# Create tiers via API (superuser only)
POST /api/v1/tiers
{
    "name": "premium",
    "description": "Premium subscription with higher limits"
}

# Assign tier to user
PUT /api/v1/users/{user_id}/tier
{
    "tier_id": 2
}
```

### Setting Rate Limits

```python
# Create rate limits per tier and endpoint
POST /api/v1/tier/premium/rate_limit
{
    "name": "premium_posts_limit",
    "path": "/api/v1/posts",
    "limit": 100,        # 100 requests
    "period": 3600       # per hour
}

# Different limits for different endpoints
POST /api/v1/tier/free/rate_limit  
{
    "name": "free_ai_limit",
    "path": "/api/v1/ai/generate",
    "limit": 5,          # 5 requests  
    "period": 86400      # per day
}
```

## Usage Patterns

### General Protection

No wiring needed — the ASGI middleware covers every non-exempt path with
tier-aware policy. To make a path *cheaper* for a tier, add a tier path
policy via the rate-limits API rather than endpoint dependencies.

### Endpoint-Specific Budgets

```python
from src.app.core.security.rate_policy import check_security_budget

@router.post("/login")
async def login(body: LoginRequest):
    # Sensitive endpoint: identity-keyed brute-force budget
    verdict = await check_security_budget("auth", client_ip)
    if verdict.is_limited:
        raise RateLimitException("Too many attempts. Try again later.")
    ...

# Adding a new budget: declare it in SECURITY_BUDGETS, then call with an
# identity (IP, user id, email hash — whatever the abuse vector is).
```

### AI adapter (sanctioned HTTPException shape)

`modules/ai/middleware.py` keeps its Depends adapter because its 429
carries Retry-After and X-RateLimit headers with a structured detail body;
the decision itself comes from `check_security_budget("ai_chat", user_id)`.


### Custom Error Handling

```python
from app.core.exceptions.http_exceptions import RateLimitException

@app.exception_handler(RateLimitException)
async def rate_limit_handler(request: Request, exc: RateLimitException):
    """Custom rate limit error response."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": 60  # Suggest retry time
        },
        headers={"Retry-After": "60"}
    )
```

## Monitoring and Analytics

### Rate Limit Metrics

```python
@router.get("/admin/rate-limit-stats")
async def get_rate_limit_stats():
    """Monitor rate limiting effectiveness."""
    
    # Get Redis statistics
    redis_info = await rate_limiter.client.info()
    
    # Count current rate limit keys
    pattern = "ratelimit:*"
    keys = await rate_limiter.client.keys(pattern)
    
    # Analyze by endpoint
    endpoint_stats = {}
    for key in keys:
        parts = key.split(":")
        if len(parts) >= 3:
            endpoint = parts[2]
            endpoint_stats[endpoint] = endpoint_stats.get(endpoint, 0) + 1
    
    return {
        "total_active_limits": len(keys),
        "redis_memory_usage": redis_info.get("used_memory_human"),
        "endpoint_stats": endpoint_stats
    }
```

### User Analytics

```python
async def analyze_user_usage(user_id: int, days: int = 7):
    """Analyze user's API usage patterns."""
    
    # This would require additional logging/analytics
    # implementation to track request patterns
    
    return {
        "user_id": user_id,
        "tier": "premium",
        "requests_last_7_days": 2540,
        "average_requests_per_day": 363,
        "top_endpoints": [
            {"path": "/api/v1/posts", "count": 1200},
            {"path": "/api/v1/users", "count": 800},
            {"path": "/api/v1/ai/generate", "count": 540}
        ],
        "rate_limit_hits": 12,  # Times user hit rate limits
        "suggested_tier": "enterprise"  # Based on usage patterns
    }
```

## Best Practices

### Rate Limit Design

```python
# Design limits based on resource cost
expensive_endpoints = {
    "/api/v1/ai/generate": {"limit": 10, "period": 3600},    # AI is expensive
    "/api/v1/reports/export": {"limit": 3, "period": 86400}, # Export is heavy
    "/api/v1/bulk/import": {"limit": 1, "period": 3600},     # Import is intensive
}

# More generous limits for lightweight endpoints  
lightweight_endpoints = {
    "/api/v1/users/me": {"limit": 1000, "period": 3600},     # Profile access
    "/api/v1/posts": {"limit": 300, "period": 3600},         # Content browsing
    "/api/v1/search": {"limit": 500, "period": 3600},        # Search queries
}
```

### Production Considerations

```python
# Set appropriate Redis memory policies
# maxmemory-policy volatile-lru  # Remove expired rate limit keys first

# Monitor Redis memory usage
# Rate limit keys can accumulate quickly under high load

# Consider rate limit key cleanup
async def cleanup_expired_rate_limits():
    """Clean up expired rate limit keys."""
    pattern = "ratelimit:*"
    keys = await redis_client.keys(pattern)
    
    for key in keys:
        ttl = await redis_client.ttl(key)
        if ttl == -2:  # Key expired but not cleaned up
            await redis_client.delete(key)
```

### Security Considerations

```python
# Rate limit by IP for unauthenticated users
if not user:
    user_id = request.client.host if request.client else "unknown"
    limit, period = DEFAULT_LIMIT, DEFAULT_PERIOD

# Prevent rate limit enumeration attacks
# Don't expose exact remaining requests in error messages

# Use progressive delays for repeated violations
# Consider temporary bans for severe abuse

# Log rate limit violations for security monitoring
if is_limited:
    logger.warning(
        f"Rate limit exceeded",
        extra={
            "user_id": user_id,
            "path": path,
            "ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent")
        }
    )
```

## Common Use Cases

### API Monetization

```python
# Different tiers for different pricing levels
tiers = {
    "free": {"daily_requests": 1000, "cost": 0},
    "starter": {"daily_requests": 10000, "cost": 29},
    "professional": {"daily_requests": 100000, "cost": 99},
    "enterprise": {"daily_requests": 1000000, "cost": 499}
}
```

### Resource Protection

```python
# Protect expensive operations via tier path policies (no endpoint wiring):
# POST /api/v1/tier/free/rate_limit
# {"name": "free_ai_limit", "path": "/api/v1/ai/generate-image",
#  "limit": 5, "period": 86400}
# Expensive paths get cheap per-tier budgets; the ASGI middleware enforces.
```

### Abuse Prevention

```python
# Sensitive endpoints use identity-keyed budgets:
from src.app.core.security.rate_policy import check_security_budget

verdict = await check_security_budget("auth", client_ip)          # login spam
verdict = await check_security_budget("magic_link_send", email_hash)  # mail-bomb
verdict = await check_security_budget("ai_chat", str(user_id))    # API cost abuse
```

This comprehensive rate limiting system provides robust protection against API abuse while supporting flexible business models through user tiers and granular endpoint controls. 
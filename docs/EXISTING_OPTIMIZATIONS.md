# Existing Optimizations Reference

**Document Purpose**: This file documents the **16 performance patterns already optimized** in the codebase. Future work should **build upon these patterns** rather than re-inventing them.

**Created**: 2026-01-20
**Current**: R1-OPUS Optimizations Round 1

---

## 1. Bloom Filter for Token Blacklist

**Location**: `src/app/core/security/bloom_filter.py`

**Pattern**: Probabilistic membership testing pre-filter

**Performance Impact**: 
- Reduces Redis queries by ~99% for token blacklist checks
- Zero false negatives (safe)
- ~1% false positive rate (acceptable)

**Implementation Details**:
```python
# Pattern: Check bloom first, only hit Redis on positive
if not bloom_filter.might_contain(token):
    return False  # Definitely not blacklisted

# Bloom positive → check Redis for definitive answer
return await token_blacklist_cache.is_blacklisted(token)
```

**Why This Works**: Bloom filters use minimal memory (bit array) but provide huge reduction in database load.

---

## 2. Atomic Lua Scripts for Redis Operations

**Locations**: 
- `src/app/core/security/rate_limit.py:132-141`
- `src/app/core/security/token_blacklist_cache.py:79-89`

**Pattern**: Multi-operation atomicity via Lua

**Performance Impact**:
- Single roundtrip vs multiple
- Prevents race conditions
- Removes need for client-side locking

**Implementation Details**:
```python
# Pattern: Use Redis EVAL for atomic multi-ops
atomic_script = """
    local key = KEYS[1]
    local expiry = tonumber(ARGV[1])
    local max_requests = tonumber(ARGV[2])
    
    local current = redis.call("INCR", key)
    if current == 1 then
        redis.call("EXPIRE", key, expiry / 1000)
    end
    return current
""
```

**Why This Works**: Reduces network latency and prevents inconsistencies from client crash mid-transaction.

---

## 3. Bitmask Permissions (Hot Path)

**Location**: `src/app/core/rbac/bitmask.py`

**Pattern**: Integer bitwise operations vs string sets

**Performance Impact**:
- O(1) authorization check via `user_permissions & required_permission > 0`
- Single integer compare vs set operations
- Memory efficient (4 bytes vs multiple strings)

**Implementation Details**:
```python
# Pattern: Bitwise AND for O(1) authorization
if user_mask & required_permission:
    # User has permission
    return True
```

**Why This Works**: Bitwise operations are CPU-level instructions (single cycle).

---

## 4. ORJSON High-Performance JSON

**Location**: `src/app/core/json_utils.py`

**Pattern**: Fast JSON library for high-throughput paths

**Performance Impact**:
- 3-10x faster than stdlib json
- Binary format compatible
- Already used in >20 hot paths

**Implementation Details**:
```python
# Pattern: Use orjson for hot path JSON
from src.app.core.json_utils import json_dumps, json_loads

# In API responses:
class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json_dumps(content)
```

**Why This Works**: ORJSON is written in Rust with zero-copy serialization.

---

## 5. Request-Scoped JWT Caching

**Location**: `src/app/core/security/jwt_utils.py:36-44`

**Pattern**: Cache decoded JWT per request

**Performance Impact**:
- Saves 70-140μs per repeated JWT access in same request
- Common in middleware chains (auth, tenant, audit)

**Implementation Details**:
```python
# Pattern: Cache in request.state on first access
def get_current_user(request: Request) -> User:
    if not hasattr(request.state, "jwt_payload"):
        request.state.jwt_payload = decode_jwt_token(token)
    return request.state.jwt_payload
```

**Why This Works**: Single decode vs N decodes per request where N = middleware count.

---

## 6. Redis Pipeline Batching

**Locations**:
- `src/app/core/utils/cache.py:305-312`
- `src/app/core/security/token_blacklist_cache.py:79-89`

**Pattern**: Batch multiple Redis commands

**Performance Impact**:
- Single network roundtrip vs N roundtrips
- Up to 50% latency reduction for multi-key operations

**Implementation Details**:
```python
# Pattern: Use async with for pipeline
pipeline = redis_client.pipeline()
for key in cache_keys:
    pipeline.get(key)
results = await pipeline.execute()
```

**Why This Works**: Network latency dominates vs command execution time.

---

## 7. GCRA Rate Limiting (Smooth)

**Location**: `src/app/core/utils/rate_limit.py`

**Pattern**: Generic Cell Rate Algorithm vs fixed window

**Performance Impact**:
- Smoother user experience
- Allows brief bursts (within rate)
- No boundary effects

**Implementation Details**:
```python
# Pattern: GCRA using Redis Lua
theory = "(TAT - now) / period <= max_requests"
```

**Why This Works**: GCRA maintains "theoretical arrival time" allowing burstiness while staying within average rate.

---

## 8. XFetch Stampede Protection

**Location**: `src/app/core/utils/cache.py:224-251`

**Pattern**: Probabilistic early cache refresh

**Performance Impact**:
- Prevents cache stampedes (thundering herd)
- Maintains cache freshness without blocking

**Implementation Details**:
```python
# Pattern: XFetch algorithm
if has_expired:
    if random.random() < recompute_probability:
        # Recompute in background
        asyncio.create_task(recompute())
    return stale_value
```

**Why This Works**: Spreads recomputation across time window rather than all at expiry.

---

## 9. Pre-Compiled Regular Expressions

**Location**: `src/app/core/utils/cache.py:16-20`

**Pattern**: Compile regex at module import

**Performance Impact**:
- ~5.8μs savings per match
- Avoids repeated compilation

**Implementation Details**:
```python
# Pattern: Compile at module level
TENANT_SCOPED_KEY_PATTERN = re.compile(r"^(tenant|public|global):([^:]+):(.+)$")

def parse_key(key: str) -> tuple[str, str, str]:
    match = TENANT_SCOPED_KEY_PATTERN.match(key)
    # Use groups...
```

**Why This Works**: `re.compile()` is O(n) in regex pattern length - do once at import.

---

## 10. Set-Based Tenant Cache Invalidation

**Location**: `src/app/core/utils/cache.py:391-411`

**Pattern**: Maintain key index sets per tenant

**Performance Impact**:
- O(1) cache invalidation
- No SCAN operations needed

**Implementation Details**:
```python
# Pattern: Maintain tenant key sets
# {tenant_scoped_key_set}:{tenant_id} -> {set of cache keys}
invalidated = 0
for key in key_set.get(tenant_id, set()):
    await redis_client.delete(key)
    invalidated += 1
```

**Why This Works**: Tracked during write, instant during delete vs expensive SCAN.

---

## 11. Negative Caching (Cache Penetration Protection)

**Location**: `src/app/core/tenancy/tenant.py:13-16,150-155`

**Pattern**: Cache "not found" results

**Performance Impact**:
- Prevents cache penetration attacks
- Reduces database load

**Implementation Details**:
```python
# Pattern: Cache negative results with short TTL
MISS_MARK = "$MISS|5m"

cached = await redis_client.get(key)
if cached == MISS_MARK:
    return None

# If not cached, check DB
tenant = await db.execute(...)  
if not tenant:
    await redis_client.setex(key, MISS_TTL, MISS_MARK)
    return None
```

**Why This Works**: DB rounds for missing data (common in attacks) eliminated.

---

## 12. Circuit Breakers for External Services

**Location**: `src/app/core/circuit_breaker.py`

**Pattern**: Fail fast on service degradation

**Performance Impact**:
- Prevents request queuing when external service is down
- Fast failure response

**Implementation Details**:
```python
# Pattern: Track failures, trip circuit
circuit = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60,
)

async def call_external():
    if circuit.is_open:
        raise CircuitOpenError()
    
    try:
        result = await external_api()
        circuit.record_success()
        return result
    except Exception:
        circuit.record_failure()
        raise
```

**Why This Works**: Prevents resource exhaustion from waiting on failing services.

---

## 13. Connection Pooling (Database + Redis)

**Locations**:
- `src/app/core/db/database.py:42-48`
- Redis connection pooling configured globally

**Pattern**: Reuse connections efficiently

**Performance Impact**:
- Avoid connection establishment overhead
- Bounded resource usage

**Configuration**:
```python
# Pattern: SQLAlchemy asyncpg with pooling
pool_size=20, max_overflow=10, pool_timeout=30
create_engine(
    settings.DATABASE_URL,
    connect_args={"server_settings": {"jit": "off"}},
    pool_pre_ping=True,
)
```

**Why This Works**: Connection establishment is expensive (TCP + auth).

---

## 14. __slots__ Memory Optimization

**Location**: `src/app/core/security/bloom_filter.py:54-58`

**Pattern**: Limit instance attributes to reduce memory

**Performance Impact**:
- ~30% memory reduction per instance
- Faster attribute access

**Implementation Details**:
```python
# Pattern: Define __slots__ for high-count instances
class BloomFilter:
    __slots__ = (
        "_size",
        "_hash_count", 
        "_bit_array",
        "_item_count",
        "_hash_fn1",
        "_hash_fn2",
        "_string_fn",
    )
```

**Why This Works**: Avoids per-instance `__dict__` overhead.

---

## 15. ARQ ORJSON Serialization (Not Pickle!)

**Location**: `src/app/core/worker/settings.py:34-41`

**Pattern**: Use orjson for background job serialization

**Performance Impact**:
- Faster serialization than pickle
- Type-safe
- No pickle security risks

**Implementation Details**:
```python
# Pattern: ARQ settings
job_serializer=functools.partial(
    orjson.dumps, option=orjson.OPT_NON_STR_KEYS
),
job_deserializer=orjson.loads,
```

**Why This Works**: orjson is 2-3x faster and safer than pickle.

---

## 16. Event Handler Timeout Protection

**Location**: `src/app/core/events.py:150-190`

**Pattern**: asyncio.wait_for wrapping

**Performance Impact**:
- Prevents slow handlers from blocking event processing
- Respectable behavior under failure

**Implementation Details**:
```python
# Pattern: Timeout on handler execution
async def handle(self, event):
    try:
        result = await asyncio.wait_for(
            self.handler(event),
            timeout=self.timeout
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(f"Handler {self} timed out")
        return None
```

**Why This Works**: Ensures system responsiveness even with misbehaving handlers.

---

## How to Use This Document

### For New Features
- **Build upon** these patterns rather than re-inventing
- Use similar patterns for analogous problems
- Consistency aids maintainability

### For Code Review
- **Verify new code follows** established patterns
- Question deviations from proven patterns
- Maintain the performance culture

### For Troubleshooting
- If something is slow, check if **not using** these patterns
- Missing pattern might indicate optimization opportunity
- Compare with existing successful implementations

### For Onboarding
- **Must-read** for new team members
- Understand the performance constraints
- Learn established patterns before writing code

---

## Performance-first Culture

These optimizations represent a **performance-conscious codebase** where:

1. **Hot paths are identified** (JWT decode, auth checks, cache lookups)
2. **Appropriate data structures** are selected (O(1) vs O(n))
3. **Library choices** are deliberate (orjson, xxhash, bitarray)
4. **Memory efficiency** is considered (__slots__, bitarray)
5. **Scalability** is planned (connection pooling, circuit breakers)

### Key Principles from These Patterns

1. **Measure Before Optimize**: Most patterns come from profiling evidence
2. **Right Tool for Job**: Use appropriate data structure for complexity class
3. **Latency Matters**: Micro-optimizations add up (70μs JWT → 5ms total request)
4. **Memory is Money**: Efficient memory = more concurrent connections
5. **Safety First**: Some patterns are correctness-critical (session management)

---

## Files Referenced

- `src/app/core/security/bloom_filter.py`
- `src/app/core/security/session_manager.py`
- `src/app/core/security/token_blacklist_cache.py`
- `src/app/core/util/rate_limit.py`
- `src/app/core/rbac/bitmask.py`
- `src/app/core/rbac/rate_limit.py`
- `src/app/core/utils/cache.py`
- `src/app/core/worker/settings.py`
- `src/app/core/events.py`
- `src/app/core/db/database.py`
- `src/app/core/circuit_breaker.py`

---

**Document maintained by**: Performance Team
**Last Reviewed**: 2026-01-20
**Next Review**: On major version bump or service architecture change

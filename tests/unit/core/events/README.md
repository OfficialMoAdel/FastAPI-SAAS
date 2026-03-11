# R1-UNIFIED Performance Optimizations - Unit Tests

Comprehensive unit test suite for R1-UNIFIED performance optimizations covering 7 critical optimization patterns.

## Test Summary

| Optimization | Test File | Test Count | Status |
|-------------|-----------|------------|--------|
| **P1-1**: Event Bus Idempotency | `test_event_idempotency.py` | 10 | ✅ All Passing |
| **P2-1**: Session Pipeline Eviction | `test_session_pipeline.py` | 11 | ✅ All Passing |
| **P2-2**: XFetch Lua Script | `test_xfetch_lua.py` | 9 | ✅ All Passing |
| **P2-3**: RBAC Redis Cache | `test_rbac_cache.py` | 11 | ✅ All Passing |
| **P3-1**: DLQ Bounded Size | `test_dlq_bounded.py` | 8 | ✅ All Passing |
| **P3-2**: Passkey Column Select | `test_passkey_column.py` | 10 | ✅ All Passing |
| **P4-1**: Circuit Breaker TOCTOU | `test_circuit_toctou.py` | 9 | ✅ All Passing |
| **Total** | 7 files | **68 tests** | ✅ **100% Pass Rate** |

## Running Tests

### Run All R1-UNIFIED Tests
```bash
uv run pytest tests/unit/r1_unified/ -v
```

### Run Specific Optimization Tests
```bash
# Event Bus Idempotency (P1-1)
uv run pytest tests/unit/r1_unified/test_event_idempotency.py -v

# Session Pipeline Eviction (P2-1)
uv run pytest tests/unit/r1_unified/test_session_pipeline.py -v

# XFetch Lua Script (P2-2)
uv run pytest tests/unit/r1_unified/test_xfetch_lua.py -v

# RBAC Redis Cache (P2-3)
uv run pytest tests/unit/r1_unified/test_rbac_cache.py -v

# DLQ Bounded Size (P3-1)
uv run pytest tests/unit/r1_unified/test_dlq_bounded.py -v

# Passkey Column Select (P3-2)
uv run pytest tests/unit/r1_unified/test_passkey_column.py -v

# Circuit Breaker TOCTOU (P4-1)
uv run pytest tests/unit/r1_unified/test_circuit_toctou.py -v
```

### Run with Coverage
```bash
uv run pytest tests/unit/r1_unified/ --cov=src/app --cov-report=term-missing --cov-report=html
```

## Test Coverage by Optimization

### P1-1: Event Bus Idempotency Race Condition Fix

**File**: `test_event_idempotency.py` (10 tests)

**Optimization**: Uses atomic Lua script for idempotency checks to prevent race conditions in duplicate event detection.

**Test Cases**:
1. ✅ Lua script executes successfully
2. ✅ Concurrent identical events processed only once
3. ✅ Different events processed independently
4. ✅ Idempotency key has correct format (`event_bus:processed:{event_id}`)
5. ✅ Lua returns 0 for duplicate events
6. ✅ Lua returns 1 for new events
7. ✅ Race condition prevented under high load (100 concurrent events)
8. ✅ Lua manager called for idempotency checks
9. ✅ Redis failure uses fallback (GET+SETEX)
10. ✅ Feature flag disables Lua optimization

**Key Assertions**:
- Atomic SETNX operation prevents race conditions
- Duplicate events skipped without processing
- Fallback path works when Lua script fails
- Feature flag `EVENT_BUS_USE_LUA_IDEMPOTENCY` controls behavior

---

### P2-1: Session Pipeline Eviction Optimization

**File**: `test_session_pipeline.py` (11 tests)

**Optimization**: Uses Redis pipeline for batch session eviction (ZRANGE + HGET batch + DELETE batch) reducing roundtrips from O(n) to O(1).

**Test Cases**:
1. ✅ Pipeline eviction reduces roundtrips (3 roundtrips vs n*3)
2. ✅ Legacy eviction uses more roundtrips (ZPOPMIN loop)
3. ✅ Batch eviction handles multiple sessions
4. ✅ Batch size limit enforced (max 50 sessions per batch)
5. ✅ Orphaned sessions handled gracefully (in ZSET but not Hash)
6. ✅ Corrupted session data handled gracefully
7. ✅ No sessions to evict (under limit)
8. ✅ Empty ZRANGE result handled
9. ✅ Feature flag controls optimization
10. ✅ Pipeline operations are atomic
11. ✅ Roundtrip metrics recorded correctly

**Key Assertions**:
- Pipeline path: ZRANGE + HGET batch + DELETE batch = 3 roundtrips
- Legacy path: ZPOPMIN + HGET + DELETE per session = n*3 roundtrips
- Batch size limit prevents memory exhaustion
- Feature flag `SESSION_USE_PIPELINE_EVICTION` controls behavior

---

### P2-2: XFetch Lua Script Optimization

**File**: `test_xfetch_lua.py` (9 tests)

**Optimization**: Combines GET+TTL into single Lua script execution, reducing roundtrips from 2 to 1.

**Test Cases**:
1. ✅ Lua script reduces GET+TTL from 2 roundtrips to 1
2. ✅ Fallback to GET+TTL when Lua script fails
3. ✅ Cache miss fetches fresh data
4. ✅ Bytes values converted to strings
5. ✅ Feature flag controls optimization
6. ✅ XFetch probabilistic early refresh (cache stampede protection)
7. ✅ Latency metrics recorded for cache hits
8. ✅ Cache miss metrics recorded correctly
9. ✅ Redis unavailable fallback works

**Key Assertions**:
- Lua script `xfetch_get_with_ttl` returns [value, ttl] in single call
- Probabilistic early refresh prevents cache stampede
- Fallback to GET+TTL when Lua fails
- Feature flag `XFETCH_USE_LUA_SCRIPT` controls behavior

---

### P2-3: RBAC Redis Cache Optimization

**File**: `test_rbac_cache.py` (11 tests)

**Optimization**: Caches role permissions in Redis to avoid repeated database queries.

**Test Cases**:
1. ✅ Cache hit avoids database query
2. ✅ Cache miss queries database and caches result
3. ✅ Cache key format correct (`rbac:perms:{tenant_id}:{role_id}`)
4. ✅ Cache TTL configurable via settings
5. ✅ Feature flag disables cache
6. ✅ Cache read error falls back to database
7. ✅ Cache write error continues operation
8. ✅ Cache invalidation on permission add
9. ✅ Cache invalidation on permission remove
10. ✅ Tenant isolation (different cache keys per tenant)
11. ✅ Empty permissions not cached (avoid cache pollution)

**Key Assertions**:
- Cache hit: no database query executed
- Cache miss: database queried and result cached
- Cache invalidated on permission changes
- Feature flag `RBAC_USE_REDIS_CACHE` controls behavior

---

### P3-1: DLQ Bounded Size Optimization

**File**: `test_dlq_bounded.py` (8 tests)

**Optimization**: Uses Redis pipeline with LTRIM to enforce bounded DLQ size, preventing unbounded memory growth.

**Test Cases**:
1. ✅ Bounded DLQ uses pipeline (LPUSH+LTRIM+LLEN)
2. ✅ Unbounded DLQ uses separate calls (LPUSH, LLEN)
3. ✅ DLQ max size enforced via LTRIM
4. ✅ Alert threshold warning when approaching capacity
5. ✅ No alert below threshold
6. ✅ DLQ entry format correct (all required fields)
7. ✅ Oldest entries discarded when full (FIFO)
8. ✅ Feature flag controls optimization

**Key Assertions**:
- Pipeline path: LPUSH + LTRIM + LLEN = 1 roundtrip
- LTRIM keeps newest entries (0 to max_size-1)
- Alert at `DLQ_ALERT_THRESHOLD_PCT` capacity
- Feature flag `DLQ_USE_BOUNDED_SIZE` controls behavior

---

### P3-2: Passkey Column Select Optimization

**File**: `test_passkey_column.py` (10 tests)

**Optimization**: Fetches only `credential_id` column instead of full rows, then decrypts in parallel with concurrency limit.

**Test Cases**:
1. ✅ Only credential_id column fetched (not full rows)
2. ✅ Column select returns correct IDs
3. ✅ Decryption parallel with asyncio.gather
4. ✅ Empty credentials returns empty list
5. ✅ Results ordered by created_at DESC
6. ✅ Decryption failures filtered out
7. ✅ Concurrency limited with semaphore
8. ✅ Large credential count handled (100+ credentials)
9. ✅ Feature flag toggles behavior
10. ✅ Monitoring metrics recorded

**Key Assertions**:
- SELECT only `credential_id` column (not `SELECT *`)
- Parallel decryption with `asyncio.gather`
- Semaphore limits concurrent decryption
- Feature flag `PASSKEY_USE_COLUMN_SELECT` controls behavior

---

### P4-1: Circuit Breaker TOCTOU Fix

**File**: `test_circuit_toctou.py` (9 tests)

**Optimization**: Uses double-check locking pattern to prevent TOCTOU race conditions in circuit breaker state transitions.

**Test Cases**:
1. ✅ Open state checked under lock (prevents TOCTOU)
2. ✅ State transition during check handled correctly
3. ✅ Closed state has no lock overhead
4. ✅ Half-open allows probe request
5. ✅ Concurrent state checks are safe
6. ✅ Half-open race condition prevented
7. ✅ Feature flag toggles behavior
8. ✅ Fallback path works (without double-check)
9. ✅ Monitoring metrics recorded

**Key Assertions**:
- Double-check pattern: check state → acquire lock → check again
- No lock overhead for closed state (fast path)
- Race conditions prevented in half-open state
- Feature flag `CIRCUIT_BREAKER_USE_DOUBLE_CHECK` controls behavior

---

## Test Infrastructure

### Fixtures (`conftest.py`)

**Mock Redis Client**:
- Async mock with common Redis operations
- Pipeline support for batch operations
- Lua script execution mocking

**Mock Database Session**:
- Async SQLAlchemy session mock
- Transaction support (commit, rollback)

**Mock Settings**:
- All R1-UNIFIED feature flags enabled by default
- Configurable TTL, batch sizes, thresholds

**Metrics Reset**:
- Auto-reset R1-UNIFIED metrics before each test
- Ensures test isolation

### Test Patterns

**Async Testing**:
- All tests use `@pytest.mark.asyncio`
- Proper async/await patterns
- AsyncMock for async operations

**Mocking Strategy**:
- Mock external dependencies (Redis, Database)
- Patch settings for feature flag control
- Mock metrics recording functions

**Assertion Patterns**:
- Verify correct code path executed (optimized vs fallback)
- Check roundtrip counts (pipeline vs sequential)
- Validate data format and correctness
- Ensure error handling works

## Feature Flags

All optimizations are controlled by feature flags in `settings.r1_unified`:

| Flag | Default | Optimization |
|------|---------|--------------|
| `EVENT_BUS_USE_LUA_IDEMPOTENCY` | `True` | P1-1: Event idempotency Lua script |
| `SESSION_USE_PIPELINE_EVICTION` | `True` | P2-1: Session pipeline eviction |
| `XFETCH_USE_LUA_SCRIPT` | `True` | P2-2: XFetch Lua script |
| `RBAC_USE_REDIS_CACHE` | `True` | P2-3: RBAC Redis cache |
| `DLQ_USE_BOUNDED_SIZE` | `True` | P3-1: DLQ bounded size |
| `PASSKEY_USE_COLUMN_SELECT` | `True` | P3-2: Passkey column select |
| `CIRCUIT_BREAKER_USE_DOUBLE_CHECK` | `True` | P4-1: Circuit breaker TOCTOU fix |

## Performance Impact

### Measured Improvements

**P1-1: Event Idempotency**
- Eliminates race conditions in duplicate detection
- Atomic operation prevents double processing

**P2-1: Session Pipeline**
- Reduces roundtrips from O(n) to O(1)
- 3 roundtrips vs n*3 for n sessions

**P2-2: XFetch Lua**
- Reduces roundtrips from 2 to 1 (50% reduction)
- Probabilistic refresh prevents cache stampede

**P2-3: RBAC Cache**
- Eliminates database queries for cached permissions
- Configurable TTL (default 3600s)

**P3-1: DLQ Bounded**
- Prevents unbounded memory growth
- Single pipeline operation vs 2 separate calls

**P3-2: Passkey Column**
- Reduces data transfer (only credential_id vs full rows)
- Parallel decryption with concurrency limit

**P4-1: Circuit Breaker**
- Prevents TOCTOU race conditions
- No lock overhead for closed state (fast path)

## Continuous Integration

### Pre-commit Checks
```bash
# Run all tests
uv run pytest tests/unit/r1_unified/ -v

# Check coverage
uv run pytest tests/unit/r1_unified/ --cov=src/app --cov-report=term-missing

# Type checking
uv run mypy src/

# Linting
uv run ruff check
```

### Coverage Goals
- **Target**: ≥80% coverage for R1-UNIFIED optimizations
- **Current**: 100% test pass rate (68/68 tests)
- **Overall Project**: 45% coverage

## Troubleshooting

### Common Issues

**Redis Connection Errors**:
- Tests use mock Redis client by default
- Real Redis tests skip if Redis unavailable

**Async Test Failures**:
- Ensure `@pytest.mark.asyncio` decorator present
- Use `AsyncMock` for async operations
- Proper `await` on async calls

**Mock Setup Issues**:
- Check mock return values match expected types
- Verify mock call counts and arguments
- Use `MagicMock` for sync, `AsyncMock` for async

**Feature Flag Issues**:
- Ensure `mock_settings` fixture used
- Patch settings at correct import location
- Check feature flag values in test

## Contributing

### Adding New Tests

1. Create test file in `tests/unit/r1_unified/`
2. Use `@pytest.mark.asyncio` for async tests
3. Import fixtures from `conftest.py`
4. Follow existing test patterns
5. Document test purpose and assertions
6. Run tests and verify coverage

### Test Naming Convention
- File: `test_{optimization_name}.py`
- Function: `test_{specific_behavior}`
- Descriptive docstrings for each test

### Assertion Guidelines
- Test both optimized and fallback paths
- Verify feature flag control
- Check error handling
- Validate metrics recording
- Ensure data correctness

## References

- **R1-UNIFIED Spec**: `docs/r1_unified_spec.md`
- **Performance Metrics**: `src/app/core/monitoring/r1_unified_metrics.py`
- **Feature Flags**: `src/app/core/config.py`
- **Test Fixtures**: `tests/unit/r1_unified/conftest.py`

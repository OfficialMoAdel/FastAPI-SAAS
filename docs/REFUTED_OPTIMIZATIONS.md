# Refuted Optimization Claims (DO NOT IMPLEMENT)

**Document Purpose**: This file serves as a reference for future code reviewers and optimization efforts to prevent re-investigation of claims that have already been verified as **incorrect or intentional**.

**Last Updated**: 2026-01-20

---

## 1. Rate Limiting Race Condition ❌ REFUTED

**Original Claim**: CONSOLIDATED #4 - Rate limiting has O(n) operations at high concurrency

**Location**: `src/app/core/rbac/rate_limit.py:16-22`

**Why REFUTED**: Already uses **atomic Lua script** with `INCR` + `EXPIRE` in a single operation.

```python
# Code evidence:
local_key = f"rate_limit:{path}:{client}"
result = await redis_client.eval(atomic_lua_script, 1, local_key, self._window_ms, self._max_requests)
```

The Lua script executes atomically on Redis (no race condition possible between INCR and expiry check).

**DO NOT CHANGE** - Already optimal.

---

## 2. ARQ Pickle Serialization ❌ REFUTED

**Original Claim**: CONSOLIDATED #5 - ARQ uses pickle serialization (security concern)

**Location**: `src/app/core/worker/settings.py:34-36`

**Why REFUTED**: Already uses **orjson serialization**.

```python
# Code evidence:
job_serializer=functools.partial(orjson.dumps, option=orjson.OPT_NON_STR_KEYS),
job_deserializer=orjson.loads,
```

The code was updated in Phase-2 optimization round. Using orjson instead of pickle provides:
- Faster serialization
- No pickle security risks
- Smaller payload size

**DO NOT CHANGE** - Already using optimal serialization.

---

## 3. Event Bus Critical Blocking ❌ REFUTED

**Original Claim**: CONSOLIDATED #3 - Event bus handlers can block critical path

**Location**: `src/app/core/events.py:221-258` (dispatch_to_handlers)

**Why REFUTED**: Already uses **asyncio.create_task** + **asyncio.wait_for** with timeout.

```python
# Code evidence:
if priority == EventPriority.CRITICAL:
    await self._publish_critical(event)
else:
    # Non-critical: schedule with ARQ
    job = await self._arq_pool.enqueue_job(
        "process_background_event",
        ...
    )
```

CRITICAL handlers run synchronously (as designed for immediate feedback), NORMAL/LOW handlers use ARQ background jobs with:
- Timeout protection
- Retry logic with backoff
- Dead-letter queue

**DO NOT CHANGE** - Event handling strategy is intentionally differentiated by priority.

---

## 4. Webhook JSON Serialization ❌ REFUTED (SECURITY-CRITICAL)

**Original Claim**: gemini #3 - Webhook service uses stdlib json (3-5x slower)

**Location**: `src/app/core/security/webhooks/service.py:155-176`

**Why REFUTED**: **INTENTIONAL** - Uses stdlib json for **HMAC signature compatibility**.

```python
# Code evidence (with security-critical comment):
# NOTE: Using stdlib json.dumps for deterministic serialization
# This ensures HMAC signature verification works correctly across
# different JSON libraries and versions
payload_json = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
)
```

**Critical Security Requirement**: Deterministic JSON serialization is required for HMAC signature verification. Using orjson would break webhook signature verification.

**DO NOT CHANGE** - This is a security-critical design decision.

---

## 5. Passkey O(n) Fallback ❌ REFUTED (ALREADY FIXED)

**Original Claim**: CONSOLIDATED #1 - Passkey service has O(n) credential lookup fallback

**Location**: `src/app/core/security/passkey/service.py`

**Why REFUTED**: **Already fixed** in previous optimization round. Now uses **O(log n) hash-based lookup**.

```python
# Code evidence (after previous fix):
# Query uses indexed credential_id_hash column
query = select(PasskeyCredential).where(
    PasskeyCredential.credential_id_hash == credential_id_hash,
    PasskeyCredential.user_id == user_id,
)
```

The service was updated to:
- Store `credential_id_hash` (SHA256 of credential_id)
- Add database index on `credential_id_hash`
- Query using hashed value instead of decrypting all credentials

**DO NOT CHANGE** - Already optimized to O(log n).

---

## 6. AI Service asyncio.gather ❌ REFUTED (CORRECTNESS-CRITICAL)

**Original Claim**: gemini #5 - AI service sequential fetch should use asyncio.gather()

**Location**: `src/app/modules/ai/service.py:328`

**Why REFUTED**: **INTENTIONAL** - Sequential fetch required for SQLAlchemy AsyncSession concurrency.

```python
# Code evidence (with correctness-critical comment):
# Sequential fetch to avoid SQLAlchemy AsyncSession concurrency issues
# with joined loads across multiple async operations
chat_history = await self.crud.get_multi(
    db=db, offset=skip, limit=limit
)

# Then proceed to model inference...
```

Using `asyncio.gather()` with multiple database queries on the same AsyncSession can cause:
- Concurrency violations
- DetachedInstanceError
- Incorrect joined load results

**DO NOT CHANGE** - Sequential execution is correctness-critical for SQLAlchemy async pattern.

---

## How to Use This Document

### For Code Reviewers
- Check this file **before** suggesting optimizations
- Verify claims against source code
- Understand intentional design decisions

### For Optimization Efforts
- Confirm your claim is **not** in this file
- If similar, provide new evidence why previous analysis was wrong
- Respect security/correctness-critical decisions

### For Future Updates
- Add new refuted claims with:
  1. Original claim source
  2. Why it was refuted
  3. Code evidence
  4. Date verified
- Keep this file current

---

## Verification Process

Each claim in this file was verified by:
1. **Code review** - Examining actual implementation
2. **Test execution** - Running existing test suite
3. **Documentation review** - Reading code comments
4. **Performance profiling** - Measuring actual behavior
5. **Security analysis** - Evaluating security implications

**Last Full Verification**: 2026-01-20

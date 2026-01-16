# Advanced Algorithmic Optimization Proposals

**Generated:** 2026-01-16
**Status:** Proposed

## Executive Summary

Following a hyper-intensive investigation of the codebase, three "gross inefficiencies" were identified where advanced algorithmic paradigms can yield order-of-magnitude improvements in specific dimensions (latency, burst protection, database load).

| Area | Current Approach | Proposed Paradigm | Impact | Confidence |
|------|------------------|-------------------|--------|------------|
| **Rate Limiting** | Fixed Window (Redis INCR) | **GCRA** (Scheduling Theory) | Smooth traffic, No bursts | High |
| **RBAC** | SQL Joins (Relational) | **Bitmasking** (GF(2) Algebra) | O(1) checks, Zero DB load | High |
| **Auth** | Redis/DB Lookup | **Bloom Filter** (Probabilistic) | 99% Redis load reduction | Medium |

---

## 1. Rate Limiting: From Fixed Window to GCRA

### The Inefficiency
The current implementation (`src/app/core/utils/rate_limit.py`) uses a **Fixed Window** algorithm via Redis `INCR`.
- **Inefficiency**: Allows "boundary bursting". If limit is 100/min, a user can send 100 requests at 11:59:59 and 100 at 12:00:00.
- **Result**: 200 requests in 1 second, defeating the purpose of "rate" limiting.

### The Solution: GCRA (Generic Cell Rate Algorithm)
Map the problem to **Scheduling Theory** (Leaky Bucket).
- **Algorithm**: Track the "Theoretical Arrival Time" (TAT) of the next allowed request.
- **State**: Only 1 key per user (the TAT timestamp).
- **Logic**:
  - `arrival = now()`
  - `tat = max(arrival, tat) + separation_interval`
  - `if tat - arrival > burst_tolerance: deny`
  - `else: allow, update tat`

### Isomorphism Proof Sketch
- **Functionality**: Both map `(user, time) -> {allow, deny}`.
- **Strictness**: GCRA is strictly "smoother". It enforces the *average* rate at a granular level.
- **State Space**: GCRA uses 1 integer (TAT) vs Fixed Window (Count + TTL).
- **Result**: Prevents bursts while maintaining the same long-term throughput.

### Opportunity Matrix
- **Impact**: High (prevents resource exhaustion attacks).
- **Effort**: Low (Lua script replacement).
- **Confidence**: High.

---

## 2. RBAC: From SQL Joins to Bitmasking (GF(2))

### The Inefficiency
The current RBAC (`src/app/modules/rbac/service.py`) relies on Relational Algebra (SQL Joins) to resolve permissions.
- **Inefficiency**: `O(D * R)` where D is hierarchy depth and R is role count. Requires DB roundtrip or complex Join for every permission check.
- **Hotspot**: Permission checks are on the "hot path" of every request.

### The Solution: Linear Algebra over GF(2)
Map permissions to a **Vector Space** (Bitsets).
- **Setup**: Assign integer ID $i \in [0, 63]$ to each permission.
- **Role**: $R_{mask} = \sum 2^{p_i}$ (Bitwise OR of permissions).
- **User**: $U_{mask} = \bigvee R_{mask}$ (Bitwise OR of assigned roles).
- **Check**: `has_permission(user, p)` $\iff (U_{mask} \land 2^p) \neq 0$.

### Isomorphism Proof Sketch
- **Input**: User $U$, Permission $P$.
- **Output**: Boolean.
- **Equivalence**: The relational set union $P_U = \bigcup P_{R_i}$ is isomorphic to the bitwise OR sum $M_U = \bigvee M_{R_i}$.
- **Performance**: Reduces SQL Join to CPU instruction (AND).

### Opportunity Matrix
- **Impact**: High (latency reduction, DB offload).
- **Effort**: Medium (Schema change, ID management).
- **Confidence**: High (if permission count < 64 or 128).

---

## 3. Token Blacklist: From Exact Lookup to Bloom Filter

### The Inefficiency
The current blacklist (`src/app/core/security/__init__.py`) checks Redis/DB for *every* token validation.
- **Inefficiency**: 99.9% of tokens are valid (not blacklisted). We pay network latency (Redis) to confirm "Empty Set".
- **Hotspot**: Every authenticated request.

### The Solution: Probabilistic Data Structures (Bloom/Cuckoo Filter)
Map the problem to **Set Membership Testing**.
- **Logic**: Check Bloom Filter first.
  - If `False`: Token is **definitely** valid. (Skip Redis).
  - If `True`: Token **might** be blacklisted. (Check Redis).
- **Constraint**: False Positives are acceptable (they just trigger a Redis check). False Negatives are impossible (security preserved).

### Isomorphism Proof Sketch
- **Invariants**:
  - Valid Token $\to$ Bloom(False) OR (Bloom(True) AND Redis(False)) $\to$ Allow.
  - Blacklisted Token $\to$ Bloom(True) AND Redis(True) $\to$ Deny.
- **Safety**: The composite function is identical to the original exact lookup.

### Opportunity Matrix
- **Impact**: Medium/High (saves ~1ms per request, reduces Redis ops by 99%).
- **Effort**: Medium (RedisBloom dependency or in-memory sync).
- **Confidence**: Medium (operational complexity).

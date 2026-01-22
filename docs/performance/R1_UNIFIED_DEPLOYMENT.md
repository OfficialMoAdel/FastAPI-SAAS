# R1-UNIFIED Deployment Runbook

## Overview

This runbook defines the safe deployment strategy for R1-UNIFIED performance optimizations with minimal risk and easy rollback.

## Feature Flags

All optimizations are controlled by feature flags in `src/app/core/config.py`:

| Flag | Optimization | Default |
|------|-------------|---------|
| `EVENT_BUS_USE_LUA_IDEMPOTENCY` | Event Bus Idempotency | `False` |
| `SESSION_USE_PIPELINE_EVICTION` | Session Eviction Pipeline | `False` |
| `XFETCH_USE_LUA_SCRIPT` | XFetch Lua Script | `False` |
| `RBAC_USE_REDIS_CACHE` | RBAC Redis Caching | `False` |
| `DLQ_USE_BOUNDED_SIZE` | DLQ Bounded Size | `True` |
| `PASSKEY_USE_COLUMN_SELECT` | Passkey Column Select | `False` |
| `CIRCUIT_BREAKER_USE_DOUBLE_CHECK` | Circuit Breaker TOCTOU | `True` |

## Deployment Phases

### Phase 1: Staging Validation

**Objective**: Validate all optimizations in staging environment

**Duration**: 1 week

**Steps**:
1. Deploy code with ALL feature flags OFF
2. Run baseline metrics:
   ```bash
   uv run python scripts/collect_baseline_metrics.py --env staging
   ```
3. Enable feature flags one by one
4. Run E2E tests for each optimization:
   ```bash
   docker-compose -f docker-compose.e2e.yml up -d
   uv run python tests/e2e/r1_unified_e2e_validation.py
   ```
5. Compare metrics against baseline:
   ```bash
   uv run python scripts/r1_unified_comparison.py
   ```
6. Verify no regressions

**Success Criteria**:
- All E2E tests pass (9/9)
- Performance improvements match targets
- No error rate increase
- No memory leaks detected
- Staging stable for 48 hours

**Rollback**: Disable feature flags via environment variables

---

### Phase 2: Canary Deployment

**Objective**: Test in production with 1% traffic

**Duration**: 1 week

**Traffic Routing**:
```python
# In middleware/canary.py
def is_canary_user(user_id: str) -> bool:
    return hash(user_id) % 100 < CANARY_PERCENTAGE
```

**Rollout Order**:
1. Event Bus Idempotency (P1-1) - 24h
2. Session Eviction (P2-1) - 24h
3. XFetch Lua (P2-2) - 24h
4. RBAC Cache (P2-3) - 24h
5. DLQ Bounded (P3-1) - 24h
6. Passkey Column (P3-2) - 24h
7. Circuit Breaker (P4-1) - 24h

**Monitoring**:
- Error rate (should not increase)
- Latency p50, p95, p99 (should decrease)
- Redis connection pool usage
- Database connection pool usage
- Memory usage
- CPU usage

**Rollback Triggers**:
- Error rate increase > 1%
- Latency increase > 20%
- Memory usage increase > 50%
- Any data consistency issues
- Redis/DB connection pool exhaustion

---

### Phase 3: Gradual Rollout

**Objective**: Increase traffic gradually to 100%

**Schedule**:

| Day | Traffic % | Action |
|-----|-----------|--------|
| 1-2 | 1% | Canary |
| 3-4 | 10% | First expansion |
| 5-6 | 25% | Quarter traffic |
| 7-8 | 50% | Half traffic |
| 9-10 | 75% | Three-quarter |
| 11-12 | 100% | Full rollout |

**Per-Day Checklist**:
- [ ] Monitor error rates
- [ ] Check latency percentiles
- [ ] Verify cache hit rates
- [ ] Review resource usage
- [ ] Confirm no alerts triggered

---

### Phase 4: Full Deployment

**Objective**: All optimizations at 100% traffic

**Duration**: 1 week observation

**Final Validation**:
```bash
# Generate final comparison report
uv run python scripts/r1_unified_comparison.py --baseline baseline_metrics.json

# Review report
cat reports/r1_unified_comparison.md
```

**Success Criteria**:
- All optimizations at 100%
- Performance targets met
- No incidents for 1 week

---

### Phase 5: Cleanup

**Objective**: Remove feature flags and finalize

**Duration**: 2 weeks after full deployment

**Steps**:
1. Remove feature flag checks from code
2. Remove old code paths
3. Update documentation
4. Archive baseline metrics

---

## Monitoring Dashboard

### Key Metrics

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Error rate | < baseline | > baseline + 1% |
| Latency p99 | < baseline | > baseline + 20% |
| Memory usage | < 80% | > 85% |
| Redis pool | < 80% | > 90% |
| DLQ size | < 10,000 | > 8,000 |

### Optimization-Specific Metrics

| Optimization | Metric | Target |
|--------------|--------|--------|
| Event Idempotency | duplicate_count | 0 |
| Session Eviction | roundtrips | -86% |
| XFetch | latency_ms | -50% |
| RBAC Cache | cache_hit_rate | > 90% |
| DLQ | queue_size | < 10,000 |
| Passkey | query_bytes | -90% |
| Circuit Breaker | race_conditions | 0 |

---

## Rollback Procedures

### Immediate Rollback (< 5 minutes)

```bash
# Disable specific feature flag
export EVENT_BUS_USE_LUA_IDEMPOTENCY=false
export SESSION_USE_PIPELINE_EVICTION=false
# ... etc

# Restart application
systemctl restart fastapi-saas
```

### Code Rollback (< 30 minutes)

```bash
# Revert to previous deployment
git checkout <previous-tag>
./deploy.sh

# Disable all R1-UNIFIED flags
export R1_UNIFIED_ENABLED=false
```

### Data Consistency Check

```sql
-- Check for orphaned sessions
SELECT COUNT(*) FROM user_sessions
WHERE user_id NOT IN (SELECT id FROM users);

-- Check DLQ size
SELECT COUNT(*) FROM dead_letter_queue;

-- Verify RBAC cache consistency
SELECT COUNT(*) FROM user_roles
WHERE updated_at > NOW() - INTERVAL '1 hour';
```

---

## Communication Templates

### Pre-Deployment

```
Subject: R1-UNIFIED Deployment Starting - Phase X

Team,

We are beginning Phase X of the R1-UNIFIED deployment.

- Start time: [TIME]
- Expected duration: [DURATION]
- On-call engineer: [NAME]
- Monitoring dashboard: [LINK]

Rollback procedure is ready if needed.
```

### Issue Detected

```
Subject: [ALERT] R1-UNIFIED Issue Detected

Team,

Issue detected during R1-UNIFIED deployment:

- Metric: [METRIC_NAME]
- Expected: [EXPECTED]
- Actual: [ACTUAL]
- Impact: [IMPACT]

Action taken: [ROLLBACK/INVESTIGATING]
```

### Deployment Complete

```
Subject: R1-UNIFIED Deployment Complete

Team,

R1-UNIFIED deployment completed successfully.

Results:
- Event Idempotency: [STATUS]
- Session Eviction: [IMPROVEMENT]%
- XFetch: [IMPROVEMENT]%
- RBAC Cache: [HIT_RATE]%
- DLQ: Bounded at [SIZE]
- Passkey: [IMPROVEMENT]%
- Circuit Breaker: [STATUS]

Full report: [LINK]
```

---

## Testing Checklist

### Before Each Phase

- [ ] All unit tests pass: `uv run pytest`
- [ ] All E2E tests pass: `uv run python tests/e2e/r1_unified_e2e_validation.py`
- [ ] Type check passes: `uv run mypy src/`
- [ ] Lint passes: `uv run ruff check`
- [ ] Performance benchmarks run
- [ ] Staging validation complete
- [ ] Rollback procedure tested
- [ ] Monitoring dashboards ready
- [ ] On-call engineer assigned
- [ ] Communication sent

---

## Risk Mitigation

### High-Risk Optimizations

| Optimization | Risk | Mitigation |
|--------------|------|------------|
| Event Bus Idempotency | Data consistency | 48h at 1%, manual validation |
| Session Eviction | Data loss | Extra monitoring, slow rollout |
| RBAC Cache | Security | Cache invalidation testing |

### Low-Risk Optimizations

| Optimization | Risk | Mitigation |
|--------------|------|------------|
| XFetch | Performance only | Standard rollout |
| DLQ Bounded | Operational | Automated monitoring |
| Passkey Column | Query optimization | Standard rollout |
| Circuit Breaker | Optional | Can disable anytime |

---

## Success Metrics

### Target Improvements

| Optimization | Metric | Target |
|--------------|--------|--------|
| Event Idempotency | duplicates | 0 |
| Session Eviction | roundtrips | -86% |
| XFetch | latency | -50% |
| RBAC Cache | latency | -90% |
| DLQ | size | ≤ 10,000 |
| Passkey | data transfer | -90% |
| Circuit Breaker | race conditions | 0 |

### Operational Metrics

- Zero data loss incidents
- Zero security incidents
- < 1 hour total downtime
- < 3 rollbacks total
- Positive team feedback

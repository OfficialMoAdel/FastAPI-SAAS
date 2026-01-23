# R2-HYBRID Deployment Checklist

## Pre-Deployment

- [ ] All tests passing: `uv run pytest tests/`
- [ ] Type check passing: `uv run mypy src/`
- [ ] Lint passing: `uv run ruff check`
- [ ] E2E validation passing: `uv run pytest tests/e2e/r2_hybrid/ -v`
- [ ] Performance comparison script passing: `python scripts/compare_performance.py`

## Phase 1: Staging Validation

### Deploy to Staging
- [ ] Deploy branch to staging environment
- [ ] Run database migrations: `uv run alembic upgrade head`
- [ ] Verify migrations applied successfully

### Run Validation Suite
- [ ] Full test suite passes on staging
- [ ] E2E tests pass on staging
- [ ] Manual smoke test of critical paths:
  - [ ] User authentication (login/logout)
  - [ ] Tenant switching
  - [ ] RBAC permission checks
  - [ ] File upload/download

### Monitor Staging (24 hours)
- [ ] Error rate: Same or lower than baseline
- [ ] Latency p95: Same or lower than baseline
- [ ] No unexpected errors in logs
- [ ] Memory usage stable (slight increase expected due to L1 caches)

## Phase 2: Canary Deployment (10% Traffic)

### Deploy Canary
- [ ] Deploy to 10% of production traffic
- [ ] Keep 90% on previous version

### Monitor Canary (2-4 hours)
- [ ] Error rate comparison: Canary vs Control
- [ ] Latency comparison: Canary vs Control
- [ ] Redis operations comparison: Canary vs Control
- [ ] Database query count comparison: Canary vs Control

### Decision Gate
- [ ] If ANY metric regresses >10%: ROLLBACK
- [ ] If all metrics stable or improved: Proceed to Phase 3

## Phase 3: Full Deployment (100% Traffic)

### Deploy Full
- [ ] Deploy to 100% of production traffic
- [ ] Run database migration (if not already run)

### Monitor (48 hours)
- [ ] Error rate stable
- [ ] Latency improved or stable
- [ ] Memory usage acceptable
- [ ] No customer complaints

### Success Confirmation
- [ ] All metrics meet targets
- [ ] No rollback needed for 48 hours
- [ ] Close epic: `bd close FastAPI_saas-m8ci`

## Rollback Plan

### Quick Rollback (Feature Flags)
Disable optimizations without code revert:
```bash
# Set environment variables to disable optimizations
export R1_UNIFIED__EVENT_BUS_USE_LUA_IDEMPOTENCY=false
export R1_UNIFIED__SESSION_USE_PIPELINE_EVICTION=false
export R1_UNIFIED__XFETCH_USE_LUA_SCRIPT=false
export R1_UNIFIED__RBAC_USE_REDIS_CACHE=false
export R1_UNIFIED__DLQ_USE_BOUNDED_SIZE=false
export R1_UNIFIED__PASSKEY_USE_COLUMN_SELECT=false
export R1_UNIFIED__CIRCUIT_BREAKER_USE_DOUBLE_CHECK=false
```

### Full Rollback (Code Revert)
If quick rollback insufficient:
```bash
# Revert to pre-optimization commit
git revert --no-commit <m8ci.20-commit>..HEAD
git commit -m "revert: rollback R2-HYBRID optimizations"
git push

# Rollback database migration (if needed)
uv run alembic downgrade wpv5_5_files_composite_index
```

### L1 Cache Emergency Clear
If L1 cache causing issues:
```python
# Clear tenant L1 cache
from src.app.middleware.tenant import _L1_TENANT_CACHE
_L1_TENANT_CACHE.clear()

# Clear RBAC L1 cache
from src.app.modules.rbac.service import _L1_RBAC_CACHE
_L1_RBAC_CACHE.clear()
```

## Monitoring Alerts

### Set Up Alerts
- [ ] Error rate spike >5%: Alert
- [ ] Latency p99 increase >20%: Alert
- [ ] Memory usage increase >30%: Alert
- [ ] Redis connection errors: Alert

### Key Metrics to Watch
| Metric | Baseline | Target | Alert Threshold |
|--------|----------|--------|-----------------|
| Error Rate | X% | <= X% | +5% |
| Latency p95 | Xms | <= Xms | +20% |
| Redis ops/s | X | <= X | +50% |
| Memory | X GB | <= X+0.5GB | +30% |

## Post-Deployment

### Documentation
- [ ] Update CHANGELOG.md
- [ ] Update performance documentation
- [ ] Archive baseline metrics

### Cleanup
- [ ] Close all m8ci subtasks
- [ ] Close m8ci epic
- [ ] Sync beads: `bd sync`
- [ ] Push all changes: `git push`

---

**Last Updated**: 2026-01-23
**Epic**: FastAPI_saas-m8ci (R2-HYBRID)

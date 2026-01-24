# Safe Deployment Strategy

**Version:** 1.0  
**Last Updated:** 2026-01-24  
**Status:** Production Ready  
**Target:** API Compatibility Updates (FastAPI SaaS)

## Executive Summary

This document outlines the safe deployment strategy for rolling out API compatibility changes to production. The strategy uses a **blue-green deployment** approach with comprehensive monitoring and instant rollback capabilities.

**Deployment Window:** 2-hour maintenance window (recommended: weekday, 2-4 AM UTC)  
**Rollback Time:** < 5 minutes for instant rollback, < 15 minutes for full rollback  
**Risk Level:** LOW (additive changes only, no breaking changes)

---

## Pre-Deployment Checklist

### ✅ Code Quality Gates

- [ ] All tests passing (unit + integration + E2E)
  ```bash
  uv run pytest tests/ -v --cov=src/app
  ```
- [ ] Type checking clean
  ```bash
  uv run mypy src/
  ```
- [ ] Linting clean
  ```bash
  uv run ruff check src/
  ```
- [ ] Frontend compatibility tests passing (34/35)
  ```bash
  uv run pytest tests/integration/test_frontend_compatibility.py -v
  ```

### ✅ Documentation

- [ ] API documentation updated (`/docs`, `/redoc`)
- [ ] AGENTS.md files created for new modules
- [ ] CHANGELOG.md updated with changes
- [ ] Migration notes documented

### ✅ Database

- [ ] Database migrations tested in staging
  ```bash
  uv run alembic upgrade head
  ```
- [ ] Migration rollback tested
  ```bash
  uv run alembic downgrade -1
  ```
- [ ] Database backup created and verified
- [ ] No destructive migrations (only additive changes)

### ✅ Staging Validation

- [ ] Deployed to staging environment
- [ ] Full test suite run on staging
- [ ] Frontend integration tested on staging
- [ ] Performance benchmarks run on staging
- [ ] Load testing completed (if applicable)

### ✅ Rollback Preparation

- [ ] Current production state documented
- [ ] Production database backup created
- [ ] Current release tagged in git
  ```bash
  git tag -a v1.0.0 -m "Pre-compatibility-update release"
  git push origin v1.0.0
  ```
- [ ] Rollback scripts tested in staging
- [ ] Incident response team notified

---

## Deployment Strategy: Blue-Green

### Why Blue-Green?

- **Zero Downtime:** Traffic switches instantly between environments
- **Instant Rollback:** Keep old version running as backup
- **Easy Testing:** Validate new version before traffic switch
- **Low Risk:** Proven strategy for production deployments

### Architecture

```
                    Load Balancer
                          |
                 ---------+---------
                 |                 |
            Blue (v1.0)       Green (v2.0)
           (Production)        (Staging)
                 |                 |
            PostgreSQL (Shared)
            Redis (Shared)
```

### Deployment Steps

#### Phase 1: Preparation (15 minutes)

1. **Create Backup** (5 minutes)
   ```bash
   # Database backup
   pg_dump -h $DB_HOST -U $DB_USER -d $DB_NAME > backup-$(date +%Y%m%d-%H%M%S).sql
   
   # Verify backup
   pg_restore --list backup-*.sql
   ```

2. **Tag Current Release** (2 minutes)
   ```bash
   git tag -a v1.0.0 -m "Production release before compatibility update"
   git push origin v1.0.0
   ```

3. **Notify Team** (5 minutes)
   - Post in #deployments channel
   - Notify on-call engineers
   - Update status page

4. **Enable Maintenance Mode** (3 minutes)
   ```bash
   # Set maintenance mode flag
   export MAINTENANCE_MODE=true
   ```

#### Phase 2: Deploy to Green (20 minutes)

1. **Pull Latest Code** (2 minutes)
   ```bash
   git fetch origin
   git checkout comp_old&new
   git pull origin comp_old&new
   ```

2. **Install Dependencies** (5 minutes)
   ```bash
   uv sync
   ```

3. **Run Database Migrations** (3 minutes)
   ```bash
   uv run alembic upgrade head
   ```

4. **Start Green Environment** (5 minutes)
   ```bash
   # Start new version on port 8001 (green)
   uv run uvicorn src.app.main:app --host 0.0.0.0 --port 8001 --workers 4
   ```

5. **Health Check Green** (5 minutes)
   ```bash
   # Run health checks
   curl http://localhost:8001/api/v1/health
   curl http://localhost:8001/api/v1/ready
   
   # Run smoke tests
   ./scripts/smoke-test.sh http://localhost:8001
   ```

#### Phase 3: Traffic Switch (10 minutes)

1. **Gradual Traffic Shift** (5 minutes)
   ```bash
   # Switch 10% traffic to green
   ./scripts/traffic-split.sh --green 10
   
   # Monitor for 2 minutes
   ./scripts/monitor.sh --duration 120
   
   # Switch 50% traffic to green
   ./scripts/traffic-split.sh --green 50
   
   # Monitor for 2 minutes
   ./scripts/monitor.sh --duration 120
   ```

2. **Full Traffic Switch** (2 minutes)
   ```bash
   # Switch 100% traffic to green
   ./scripts/traffic-split.sh --green 100
   ```

3. **Monitor Green** (3 minutes)
   - Watch error rates
   - Check response times
   - Verify frontend connectivity
   - Monitor database queries

#### Phase 4: Validation (15 minutes)

1. **Automated Tests** (5 minutes)
   ```bash
   # Run production smoke tests
   ./scripts/production-smoke-test.sh
   ```

2. **Manual Validation** (5 minutes)
   - Test critical user flows
   - Verify new endpoints work
   - Check admin dashboard
   - Test frontend integration

3. **Monitoring Check** (5 minutes)
   - Error rate < 0.1%
   - P95 latency < 200ms
   - No 500 errors
   - Database connections healthy
   - Redis cache working

#### Phase 5: Cleanup (10 minutes)

1. **Disable Maintenance Mode** (1 minute)
   ```bash
   export MAINTENANCE_MODE=false
   ```

2. **Stop Blue Environment** (2 minutes)
   ```bash
   # Keep blue running for 1 hour as safety net
   # Then stop after validation
   pkill -f "uvicorn.*8000"
   ```

3. **Update Documentation** (5 minutes)
   - Update deployment log
   - Record any issues
   - Update runbook

4. **Notify Team** (2 minutes)
   - Post success message
   - Share metrics
   - Thank team

---

## Alternative: Canary Deployment

If blue-green is not available, use canary deployment:

### Steps

1. **Deploy to 10% of servers**
   ```bash
   kubectl set image deployment/api api=api:v2.0.0 --replicas=1
   ```

2. **Monitor for 10 minutes**
   - Error rates
   - Response times
   - User complaints

3. **Increase to 50%**
   ```bash
   kubectl scale deployment/api --replicas=5
   ```

4. **Monitor for 10 minutes**

5. **Full rollout to 100%**
   ```bash
   kubectl scale deployment/api --replicas=10
   ```

---

## Feature Flags Strategy

Use feature flags for additional safety:

### Implementation

```python
# In settings
ENABLE_SECURITY_ROUTES = env.bool("ENABLE_SECURITY_ROUTES", default=False)
ENABLE_APP_CONFIG = env.bool("ENABLE_APP_CONFIG", default=True)  # Public endpoint
ENABLE_DASHBOARD = env.bool("ENABLE_DASHBOARD", default=False)
ENABLE_PERMISSIONS = env.bool("ENABLE_PERMISSIONS", default=False)
```

### Deployment with Flags

1. **Deploy code with flags OFF**
2. **Enable public endpoints first** (app_configuration)
3. **Enable authenticated endpoints gradually**
4. **Monitor each activation**

### Toggle Flags

```bash
# Enable dashboard
curl -X PATCH http://api.example.com/api/v1/feature-flags/dashboard \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"enabled": true}'
```

---

## Monitoring & Alerting

### Immediate Monitoring (First Hour)

**Metrics to Watch:**

1. **Error Rate**
   - Target: < 0.1%
   - Alert: > 0.5%
   - Critical: > 1%

2. **Response Time**
   - P50: < 50ms
   - P95: < 200ms
   - P99: < 500ms
   - Alert: P95 > 300ms

3. **Database**
   - Query time: < 100ms average
   - Connection pool: < 80% utilized
   - Alert: Pool > 90%

4. **Redis Cache**
   - Hit rate: > 80%
   - Memory: < 80% utilized
   - Alert: Memory > 90%

5. **Frontend Errors**
   - Rate: < 1 error/minute
   - Alert: > 10 errors/minute

### Dashboard Setup

**Grafana Dashboards:**

1. **API Overview**
   - Request rate
   - Error rate
   - Response time percentiles

2. **Endpoint Performance**
   - Per-endpoint success rate
   - Per-endpoint latency
   - New endpoints highlighted

3. **Database Performance**
   - Query execution time
   - Connection pool status
   - Slow query log

4. **System Health**
   - CPU usage
   - Memory usage
   - Network I/O

### Alerts Configuration

**PagerDuty/Email Alerts:**

```yaml
alerts:
  - name: high_error_rate
    condition: error_rate > 1%
    severity: critical
    action: page_oncall
    
  - name: slow_response_time
    condition: p95_latency > 500ms
    severity: warning
    action: email_team
    
  - name: database_connection_pool_exhausted
    condition: db_pool_usage > 95%
    severity: critical
    action: page_oncall
```

---

## Success Criteria

Deployment is considered successful when:

- ✅ All health checks passing
- ✅ Error rate < 0.1%
- ✅ P95 response time < 200ms
- ✅ Frontend integration working
- ✅ No database performance degradation
- ✅ No user complaints
- ✅ Monitoring shows stable metrics for 1 hour

---

## Communication Plan

### Pre-Deployment

**1 Day Before:**
- Email to stakeholders: "Deployment scheduled for [DATE] [TIME]"
- #engineering Slack: "Deployment prep starting"
- Status page: "Scheduled maintenance [DATE] [TIME]"

**2 Hours Before:**
- #deployments Slack: "Deployment starting in 2 hours"
- Confirm on-call engineer availability
- Final checklist review

### During Deployment

**Status Updates:**
- Post update every 15 minutes in #deployments
- Update status page with progress
- Escalate issues immediately to CTO/Lead Engineer

**Communication Template:**
```
[15:00] 🚀 Deployment started
[15:15] ✅ Green environment deployed
[15:30] ✅ Traffic at 10%
[15:45] ✅ Traffic at 50%
[16:00] ✅ Traffic at 100%
[16:15] ✅ Deployment complete, monitoring...
```

### Post-Deployment

**Immediate (within 30 minutes):**
- #deployments: "Deployment complete, all green ✅"
- Email stakeholders: "API update successfully deployed"
- Status page: "All systems operational"

**Follow-Up (within 24 hours):**
- Post-mortem document (if issues occurred)
- Metrics summary report
- Lessons learned

---

## Rollback Procedures

**See:** [ROLLBACK_PROCEDURES.md](./ROLLBACK_PROCEDURES.md)

Quick reference:
- **Instant Rollback:** < 5 minutes (traffic switch)
- **Full Rollback:** < 15 minutes (code revert + redeploy)
- **Database Rollback:** < 30 minutes (restore from backup)

---

## Team Responsibilities

### Deployment Lead
- Execute deployment steps
- Make go/no-go decisions
- Coordinate team communication

### On-Call Engineer
- Monitor metrics during deployment
- Respond to alerts
- Execute rollback if needed

### QA Engineer
- Run manual validation tests
- Test frontend integration
- Report any issues

### DevOps Engineer
- Ensure infrastructure ready
- Configure monitoring/alerts
- Manage blue-green switch

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Database migration failure | Low | High | Test in staging, create backup |
| High error rate after deploy | Low | Medium | Blue-green rollback ready |
| Performance degradation | Low | Medium | Load testing pre-deploy |
| Frontend breaks | Low | High | Compatibility tests, monitoring |
| User complaints | Very Low | Low | Gradual rollout, clear communication |

**Overall Risk:** **LOW** (additive changes only, comprehensive testing)

---

## Appendix

### A. Environment Variables Checklist

```bash
# Required
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
SECRET_KEY=...

# Optional (for new features)
ENABLE_DASHBOARD=true
ENABLE_SECURITY_ROUTES=true
ENABLE_PERMISSIONS=true
```

### B. Dependencies Verification

```bash
# Check all dependencies installed
uv sync --verify

# Check for security vulnerabilities
uv run pip-audit
```

### C. Database Migration Preview

```bash
# See what migrations will run
uv run alembic upgrade head --sql

# Test migration in transaction (rollback after)
uv run alembic upgrade head --sql | psql $DATABASE_URL -1
```

### D. Performance Baseline

**Current Production Metrics (pre-deployment):**
- Average response time: 45ms
- P95 response time: 120ms
- Error rate: 0.02%
- Requests/second: 500

**Expected Post-Deployment:**
- Average response time: 45-50ms (no degradation expected)
- P95 response time: 120-150ms
- Error rate: < 0.1%
- Requests/second: 500-600

---

**Next Steps:**
1. Review this deployment plan with team
2. Schedule deployment window
3. Execute pre-deployment checklist
4. Proceed with deployment following this plan
5. Execute post-deployment monitoring

**Related Documents:**
- [Rollback Procedures](./ROLLBACK_PROCEDURES.md)
- [Health Check Scripts](../scripts/health-check.sh)
- [Deployment Scripts](../scripts/deploy.sh)

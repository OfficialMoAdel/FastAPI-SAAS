# Deployment Readiness Checklist - Phase 2: Resilience & Observability

**Version:** 1.0
**Date:** 2026-01-30
**Scope:** Circuit Breaker Registry, Database Circuit Breaker, Job Monitoring Service
**Target Environment:** Production

---

## 📋 Pre-Deployment Validation

### Code Quality Gates

- [ ] **Type Checking**: `uv run mypy src/` - **MUST** show 0 errors
  ```bash
  uv run mypy src/
  # Expected: Success: no issues found
  ```

- [ ] **Linting**: `uv run ruff check` - **MUST** show 0 errors
  ```bash
  uv run ruff check
  # Expected: All checks passed
  ```

- [ ] **Unit Tests**: `uv run pytest` - **MUST** show 100% pass rate
  ```bash
  uv run pytest
  # Expected: All tests passed
  ```

- [ ] **Test Coverage**: Phase 2 code coverage >90%
  ```bash
  uv run pytest --cov=src/app/core/resilience --cov=src/app/core/job_monitoring --cov-report=term-missing
  # Expected: Coverage >90% for new modules
  ```

- [ ] **E2E Tests**: All end-to-end tests passing
  ```bash
  uv run pytest tests/e2e/
  # Expected: test_cross_phase_integration.py, test_phase2_db_circuit_breaker.py, test_phase3_job_monitoring.py all pass
  ```

### Performance Validation

- [ ] **Performance Benchmarks**: All targets met (see `tests/performance/phase2_benchmark_report.md`)
  - Circuit Breaker Registry lookup: <1ms ✅ (actual: 0.57µs)
  - DB Circuit Breaker overhead: <5ms ✅ (actual: 0.98ms)
  - Job Monitoring record start: <10ms ✅ (actual: 1.29ms)
  - Job Monitoring get metrics: <50ms ✅ (actual: 1.08ms)

### Database & Infrastructure

- [ ] **Database Migrations**: Verify no schema changes needed
  ```bash
  uv run alembic check
  # Expected: No pending migrations (Phase 2 is Redis-based only)
  ```

- [ ] **Redis Availability**: Confirm Redis connection configured
  ```bash
  # Verify REDIS_URL in environment
  echo $REDIS_URL
  # Expected: redis://localhost:6379 or production Redis URL
  ```

### Documentation & Code Review

- [ ] **AGENTS.md Updated**: All module documentation current
  - `src/app/core/AGENTS.md` - Resilience patterns documented
  - `src/app/core/db/AGENTS.md` - DB circuit breaker usage documented
  - `src/app/api/AGENTS.md` - Admin endpoints documented
  - `tests/AGENTS.md` - Test patterns documented

- [ ] **Usage Examples**: Examples directory created and validated
  - `examples/circuit_breaker_usage.py` - Registry operations
  - `examples/db_circuit_breaker_usage.py` - Query with fallback
  - `examples/job_monitoring_usage.py` - ARQ integration
  - `examples/env_validator_usage.py` - Custom validation

- [ ] **Code Review**: Peer review completed
  - Architecture review: Circuit breaker patterns appropriate
  - Security review: No new attack surface (defensive features only)
  - Performance review: Benchmarks validate <5ms overhead

### Environment Configuration

- [ ] **Environment Variables**: No new variables required for Phase 2
  - Uses existing `REDIS_URL` for circuit breaker state
  - Uses existing cache configuration for job monitoring
  - No secrets or API keys needed

---

## 🚨 Monitoring Alerts Configuration

### Alert 1: Circuit Breaker OPEN State

**Metric:** `count(circuit_state == OPEN) by service_type`
**Threshold:** Any circuit OPEN for >5 minutes
**Severity:**
- `WARNING` - Database circuit OPEN
- `INFO` - Other circuits OPEN

**Action:**
- Database circuit: Page on-call engineer immediately
- Other circuits: Slack alert to #engineering channel

**Query Example (Prometheus):**
```promql
count(circuit_breaker_state{state="OPEN"}) by (service_type) > 0
```

**Alert Configuration:**
```yaml
- alert: CircuitBreakerOpen
  expr: circuit_breaker_state{state="OPEN"} == 1
  for: 5m
  labels:
    severity: "{{ if eq .Labels.service_type \"database\" }}warning{{ else }}info{{ end }}"
  annotations:
    summary: "Circuit breaker {{ .Labels.name }} is OPEN"
    description: "Circuit breaker for {{ .Labels.service_type }} has been OPEN for >5 minutes"
```

---

### Alert 2: Circuit Breaker Health Percentage

**Metric:** `health_percentage` from `GET /api/v1/admin/circuit-breakers`
**Threshold:** <80% for >10 minutes
**Severity:** `WARNING`

**Action:** Slack alert to #engineering channel

**Query Example:**
```promql
circuit_breaker_health_percentage < 80
```

**Alert Configuration:**
```yaml
- alert: CircuitBreakerHealthLow
  expr: circuit_breaker_health_percentage < 80
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Circuit breaker health is low"
    description: "Overall circuit breaker health is {{ .Value }}% (threshold: 80%)"
```

---

### Alert 3: Job Failure Rate

**Metric:** `(failed / total)` from job monitoring metrics
**Threshold:** >10% over 1 hour window
**Severity:** `WARNING`

**Action:** Investigate DLQ and failure reasons

**Query Example:**
```promql
rate(job_failures_total[1h]) / rate(job_executions_total[1h]) > 0.10
```

**Alert Configuration:**
```yaml
- alert: JobFailureRateHigh
  expr: rate(job_failures_total[1h]) / rate(job_executions_total[1h]) > 0.10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Job failure rate is high"
    description: "Job failure rate is {{ .Value | humanizePercentage }} over the last hour"
```

---

### Alert 4: Dead Letter Queue Size

**Metric:** `dlq_size` from `GET /api/v1/admin/jobs/summary`
**Thresholds:**
- `INFO`: >100 items
- `WARNING`: >500 items
- `CRITICAL`: >1000 items

**Action:** Review failed jobs, may indicate systemic issue

**Query Example:**
```promql
job_dlq_size > 100
```

**Alert Configuration:**
```yaml
- alert: DLQSizeHigh
  expr: job_dlq_size > 100
  labels:
    severity: "{{ if gt .Value 1000.0 }}critical{{ else if gt .Value 500.0 }}warning{{ else }}info{{ end }}"
  annotations:
    summary: "Dead Letter Queue size is {{ .Value }}"
    description: "DLQ has {{ .Value }} failed jobs (thresholds: 100/500/1000)"
```

---

### Alert 5: Job Monitoring Performance

**Metric:** `avg_duration_ms` per function
**Threshold:** >2x baseline for any function
**Severity:** `INFO`

**Action:** Performance investigation

**Query Example:**
```promql
job_duration_ms > (job_duration_baseline_ms * 2)
```

**Alert Configuration:**
```yaml
- alert: JobPerformanceDegraded
  expr: job_duration_ms > (job_duration_baseline_ms * 2)
  for: 15m
  labels:
    severity: info
  annotations:
    summary: "Job {{ .Labels.function_name }} performance degraded"
    description: "Average duration {{ .Value }}ms is >2x baseline"
```

---

## 🚀 Deployment Steps

### Step 1: Pre-Deployment Preparation

- [ ] **Create Deployment Tag**
  ```bash
  git tag -a v1.0.0-phase2 -m "Phase 2: Resilience & Observability"
  git push origin v1.0.0-phase2
  ```

- [ ] **Notify Team**: Post in #engineering Slack channel
  ```
  🚀 Phase 2 deployment starting
  Features: Circuit Breaker Registry, DB Circuit Breaker, Job Monitoring
  ETA: [deployment window]
  Rollback plan: Ready
  ```

### Step 2: Staging Deployment

- [ ] **Deploy to Staging**
  ```bash
  # Deploy via CI/CD pipeline or manual deployment
  git checkout v1.0.0-phase2
  # Follow your deployment process
  ```

- [ ] **Verify Staging Startup**
  ```bash
  # Check logs for circuit breaker registration
  tail -f /var/log/app/startup.log | grep "Registered circuit breaker"
  # Expected: 4 registration messages (webhook, email, arq, database)
  ```

- [ ] **Run Smoke Tests on Staging**
  ```bash
  # Test circuit breaker endpoint
  curl https://staging.example.com/api/v1/admin/circuit-breakers
  # Expected: 200 OK with 4 circuit breakers listed

  # Test job monitoring endpoint
  curl https://staging.example.com/api/v1/admin/jobs/summary
  # Expected: 200 OK with job summary
  ```

- [ ] **Verify Admin Endpoints Accessible**
  - `GET /api/v1/admin/circuit-breakers` - Returns health summary
  - `GET /api/v1/admin/circuit-breakers/{name}` - Returns circuit details
  - `POST /api/v1/admin/circuit-breakers/reset-all` - Resets all circuits
  - `GET /api/v1/admin/jobs/metrics` - Returns job metrics
  - `GET /api/v1/admin/jobs/dlq` - Returns DLQ items

- [ ] **Monitor Staging for 30 Minutes**
  - No errors in logs
  - All circuits remain CLOSED
  - Job monitoring recording events correctly

### Step 3: Production Deployment

- [ ] **Schedule Maintenance Window** (if required)
  - Recommended: Low-traffic period
  - Duration: 15-30 minutes
  - Notify users if downtime expected

- [ ] **Deploy to Production**
  ```bash
  # Deploy via CI/CD pipeline
  git checkout v1.0.0-phase2
  # Follow production deployment process
  ```

- [ ] **Monitor Production Startup Logs**
  ```bash
  tail -f /var/log/app/startup.log | grep "circuit breaker\|job monitor"
  # Expected: 4 circuit breaker registrations, job monitor initialized
  ```

- [ ] **Verify Circuit Breaker Registration**
  ```bash
  curl https://api.example.com/api/v1/admin/circuit-breakers
  # Expected: webhook_circuit, email_circuit, arq_circuit, database_circuit all present
  ```

---

## ✅ Post-Deployment Validation

### Immediate Validation (First 15 Minutes)

- [ ] **Verify Circuit Breakers Registered**
  ```bash
  curl -H "Authorization: Bearer $ADMIN_TOKEN" \
    https://api.example.com/api/v1/admin/circuit-breakers
  ```
  **Expected Response:**
  ```json
  {
    "total": 4,
    "by_state": {"CLOSED": 4, "OPEN": 0, "HALF_OPEN": 0},
    "health_percentage": 100.0,
    "circuit_breakers": [
      {"name": "webhook_circuit", "state": "CLOSED", "service_type": "webhook"},
      {"name": "email_circuit", "state": "CLOSED", "service_type": "email"},
      {"name": "arq_circuit", "state": "CLOSED", "service_type": "redis"},
      {"name": "database_circuit", "state": "CLOSED", "service_type": "database"}
    ]
  }
  ```

- [ ] **Test Circuit Breaker Admin Operations**
  ```bash
  # Get individual circuit details
  curl https://api.example.com/api/v1/admin/circuit-breakers/database_circuit
  # Expected: 200 OK with circuit details

  # Reset all circuits (should be no-op if all CLOSED)
  curl -X POST https://api.example.com/api/v1/admin/circuit-breakers/reset-all
  # Expected: 200 OK with reset confirmation
  ```

- [ ] **Verify Job Monitoring Working**
  ```bash
  # Enqueue a test background job (use your job queue)
  # Then check metrics
  curl https://api.example.com/api/v1/admin/jobs/metrics
  # Expected: Job appears in metrics with success/failure counts
  ```

- [ ] **Check Logs for Expected Messages**
  ```bash
  # Search for registration messages
  grep "Registered circuit breaker:" /var/log/app/startup.log
  # Expected: 4 messages (webhook, email, arq, database)

  # Search for errors
  grep "ERROR" /var/log/app/app.log | grep -i "circuit\|job"
  # Expected: No Phase 2-related errors
  ```

### Performance Spot Check (First Hour)

- [ ] **Measure API Response Time**
  ```bash
  # Test circuit breaker endpoint latency
  time curl https://api.example.com/api/v1/admin/circuit-breakers
  # Expected: <100ms p99 latency
  ```

- [ ] **Check Database Query Overhead**
  - Use APM tool (DataDog, New Relic, etc.)
  - Compare query times before/after deployment
  - Expected: <5ms overhead for circuit breaker wrapper

- [ ] **Verify No Performance Degradation**
  - Check APM dashboard for overall API latency
  - Expected: No significant change in p95/p99 latencies
  - Acceptable: <5% increase due to circuit breaker overhead

### 24-Hour Monitoring

- [ ] **No Circuit Breakers Opening Unexpectedly**
  - Check dashboard: All circuits should remain CLOSED
  - If any circuit opens: Investigate root cause immediately

- [ ] **No Job Monitoring Errors**
  ```bash
  grep "ERROR.*job_monitor" /var/log/app/app.log
  # Expected: No errors
  ```

- [ ] **No Performance Degradation**
  - Monitor APM metrics for 24 hours
  - Expected: Stable latency, no memory leaks
  - Alert threshold: >20% degradation triggers investigation

- [ ] **Verify Structured Logging**
  ```bash
  # Check logs include context fields
  grep "circuit_breaker" /var/log/app/app.log | head -5
  # Expected: JSON logs with service_type, circuit_name fields
  ```

---

## 🔄 Rollback Plan

### Rollback Criteria

**Trigger rollback immediately if:**
- Any circuit breaker opens unexpectedly within first hour
- Performance degradation >20% in any critical endpoint
- ERROR logs related to Phase 2 features
- Job monitoring fails to record events
- Admin endpoints return 500 errors

### Rollback Procedure

1. **Notify Team**
   ```
   🚨 Rolling back Phase 2 deployment
   Reason: [specific issue]
   ETA: 10 minutes
   ```

2. **Execute Rollback**
   ```bash
   # Revert to previous version
   git checkout v0.9.0  # Previous stable version
   # Redeploy via CI/CD
   ```

3. **Verify Rollback Success**
   ```bash
   # Confirm Phase 2 features disabled
   curl https://api.example.com/api/v1/admin/circuit-breakers
   # Expected: 404 Not Found (endpoint removed)

   # Verify existing functionality intact
   curl https://api.example.com/api/v1/health
   # Expected: 200 OK
   ```

4. **Post-Rollback Analysis**
   - Collect logs from failed deployment
   - Identify root cause
   - Create incident report
   - Plan fix and re-deployment

### Communication Plan

- **Before Rollback**: Post in #engineering Slack with reason
- **During Rollback**: Update status every 5 minutes
- **After Rollback**: Post-mortem within 24 hours

---

## 📊 Post-Deployment Monitoring (First Week)

### Daily Checks

- [ ] **Circuit Breaker Health Percentage**
  ```bash
  curl https://api.example.com/api/v1/admin/circuit-breakers | jq '.health_percentage'
  # Expected: 100% (all circuits CLOSED)
  ```

- [ ] **Job Monitoring Success Rates**
  ```bash
  curl https://api.example.com/api/v1/admin/jobs/summary | jq '.success_rate'
  # Expected: >90% success rate
  ```

- [ ] **DLQ Size Trends**
  ```bash
  curl https://api.example.com/api/v1/admin/jobs/summary | jq '.dlq_size'
  # Expected: <100 items (stable or decreasing)
  ```

- [ ] **Performance Metrics vs Baseline**
  - Check APM dashboard daily
  - Compare p95/p99 latencies to pre-deployment baseline
  - Expected: Within 5% of baseline

### Weekly Review (End of Week 1)

- [ ] **Which Circuits Opened and Why**
  - Review logs for any circuit state changes
  - Document: Circuit name, timestamp, reason, resolution
  - Action: Adjust thresholds if false positives detected

- [ ] **Job Failure Patterns**
  - Analyze DLQ items for common failure reasons
  - Document: Top 3 failure types, affected functions
  - Action: Fix underlying issues or improve error handling

- [ ] **Performance Trends**
  - Generate weekly performance report
  - Compare: Week 1 vs baseline
  - Action: Optimize if any degradation >10%

- [ ] **Adjust Alert Thresholds**
  - Review alert frequency and false positive rate
  - Tune thresholds based on actual production behavior
  - Document: Old threshold → New threshold, reason

---

## 🎓 Team Training & Documentation

### Training Checklist

- [ ] **Admin Endpoint Training**
  - Demo: How to check circuit breaker health
  - Demo: How to reset circuits manually
  - Demo: How to investigate job failures via DLQ

- [ ] **Runbook Creation**
  - Document: "Circuit Breaker OPEN - Investigation Guide"
  - Document: "High Job Failure Rate - Troubleshooting"
  - Document: "DLQ Management - Best Practices"

- [ ] **On-Call Playbook Update**
  - Add: Circuit breaker alert response procedures
  - Add: Job monitoring alert escalation paths
  - Add: Performance degradation investigation steps

### Documentation Links

- **Architecture**: `ARCHITECTURE.md` - Resilience patterns overview
- **API Docs**: `src/app/api/v1/admin/AGENTS.md` - Admin endpoint usage
- **Examples**: `examples/` - Code examples for developers
- **Performance**: `tests/performance/phase2_benchmark_report.md` - Benchmark results

---

## ✅ Success Criteria

### Deployment Success

- [x] All pre-deployment checks passed
- [ ] All monitoring alerts configured and tested
- [ ] Staging deployment successful with no issues
- [ ] Production deployment completed within maintenance window
- [ ] All post-deployment validation checks passed

### Operational Success (24 Hours)

- [ ] No incidents or outages related to Phase 2
- [ ] No circuit breakers opened unexpectedly
- [ ] Performance metrics stable (within 5% of baseline)
- [ ] No ERROR logs related to Phase 2 features
- [ ] Team trained on new admin endpoints

### Long-Term Success (1 Week)

- [ ] Circuit breakers functioning as designed (catching real failures)
- [ ] Job monitoring providing valuable insights
- [ ] DLQ size stable or decreasing
- [ ] Performance overhead <5ms as benchmarked
- [ ] Alert thresholds tuned to production behavior

---

## 📝 Sign-Off

**Deployment Lead:** _____________________ Date: _____
**Engineering Manager:** _____________________ Date: _____
**DevOps Lead:** _____________________ Date: _____

---

## 📞 Emergency Contacts

**On-Call Engineer:** [Pager/Phone]
**Engineering Manager:** [Slack/Phone]
**DevOps Team:** #devops Slack channel
**Incident Response:** [Incident management tool]

---

**Document Version:** 1.0
**Last Updated:** 2026-01-30
**Next Review:** After first production deployment

# Rollback Procedures

**Version:** 1.0  
**Last Updated:** 2026-01-24  
**Audience:** Engineering Team, On-Call Engineers  
**Criticality:** HIGH

## Quick Reference

| Rollback Type | Time | Complexity | Data Loss Risk |
|--------------|------|------------|----------------|
| **Instant Traffic Switch** | < 5 min | Low | None |
| **Feature Flag Disable** | < 2 min | Very Low | None |
| **Code Revert + Redeploy** | < 15 min | Medium | None |
| **Database Restore** | < 30 min | High | Possible |

---

## When to Rollback

### Automatic Rollback Triggers

Execute **immediate rollback** if any of these conditions are met:

1. **Error Rate > 1%**
   ```
   Alert: Critical error rate threshold exceeded
   Action: IMMEDIATE ROLLBACK
   ```

2. **P95 Latency > 500ms**
   ```
   Alert: Response time degradation detected
   Action: IMMEDIATE ROLLBACK
   ```

3. **Frontend Error Rate > 10 errors/minute**
   ```
   Alert: Frontend breaking errors
   Action: IMMEDIATE ROLLBACK
   ```

4. **Database Connection Pool > 95%**
   ```
   Alert: Database connection pool exhausted
   Action: IMMEDIATE ROLLBACK
   ```

5. **500 Error Spike** (> 10 errors/minute)
   ```
   Alert: Server errors spiking
   Action: IMMEDIATE ROLLBACK
   ```

### Manual Rollback Criteria

Execute **manual rollback** if:

- Critical functionality broken (login, payment, etc.)
- Data corruption detected
- Security vulnerability discovered
- Multiple user complaints within 5 minutes
- Deployment lead determines rollback necessary

---

## Rollback Option 1: Instant Traffic Switch (RECOMMENDED)

**Time:** < 5 minutes  
**Risk:** Very Low  
**Use When:** Blue-green deployment, minor issues detected

### Prerequisites

- Blue environment still running on port 8000
- Green environment running on port 8001
- Load balancer can route between both

### Steps

#### 1. Switch Traffic Back to Blue (2 minutes)

```bash
# Option A: Using load balancer
./scripts/traffic-split.sh --green 0 --blue 100

# Option B: Using environment variable
export ACTIVE_ENVIRONMENT=blue

# Option C: Kubernetes
kubectl set image deployment/api api=api:v1.0.0
kubectl rollout status deployment/api
```

#### 2. Verify Blue is Serving Traffic (1 minute)

```bash
# Check health
curl http://localhost:8000/api/v1/health

# Verify active environment
curl http://load-balancer/api/v1/health | jq '.environment'
```

#### 3. Monitor Metrics (2 minutes)

```bash
# Watch error rate
./scripts/monitor.sh --duration 120

# Check specific metrics
curl http://localhost:9090/metrics | grep error_rate
```

#### 4. Stop Green Environment (Optional)

```bash
# Can keep green running for investigation
# Or stop to free resources
pkill -f "uvicorn.*8001"
```

### Validation

- ✅ Traffic routing to blue (port 8000)
- ✅ Error rate back to normal (< 0.1%)
- ✅ Response times normal
- ✅ No frontend errors

---

## Rollback Option 2: Feature Flag Disable

**Time:** < 2 minutes  
**Risk:** Very Low  
**Use When:** Specific new endpoints causing issues

### Prerequisites

- Feature flags implemented for new endpoints
- Admin access to feature flag service

### Steps

#### 1. Identify Problematic Feature (30 seconds)

```bash
# Check which endpoint has high error rate
curl http://monitoring/api/errors | jq '.endpoints[] | select(.error_rate > 0.5)'
```

#### 2. Disable Feature Flag (30 seconds)

```bash
# Option A: Using API
curl -X PATCH http://localhost:8000/api/v1/feature-flags/dashboard \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"enabled": false}'

# Option B: Using environment variable
export ENABLE_DASHBOARD=false
# Restart application
systemctl restart fastapi-app

# Option C: Using database
psql $DATABASE_URL -c "UPDATE feature_flags SET enabled=false WHERE slug='dashboard';"
```

#### 3. Verify Flag Disabled (30 seconds)

```bash
# Check feature flag status
curl http://localhost:8000/api/v1/feature-flags | jq '.[] | select(.slug=="dashboard")'

# Verify endpoint returns 404 or disabled message
curl http://localhost:8000/api/v1/dashboard
```

#### 4. Monitor Impact (30 seconds)

```bash
# Check if error rate dropped
./scripts/monitor.sh --duration 30
```

### Validation

- ✅ Feature flag disabled
- ✅ Endpoint no longer accessible
- ✅ Error rate decreased
- ✅ Other endpoints still functional

---

## Rollback Option 3: Code Revert + Redeploy

**Time:** < 15 minutes  
**Risk:** Medium  
**Use When:** Multiple issues, need full rollback

### Prerequisites

- Git access to repository
- Deployment scripts ready
- Previous version tagged in git

### Steps

#### 1. Revert to Previous Version (3 minutes)

```bash
# Option A: Revert commit
git revert HEAD --no-edit
git push origin comp_old&new

# Option B: Reset to tag
git fetch --tags
git checkout v1.0.0
git push origin comp_old&new --force

# Option C: Create rollback branch
git checkout -b rollback-$(date +%Y%m%d)
git revert HEAD
git push origin rollback-$(date +%Y%m%d)
```

#### 2. Redeploy Application (8 minutes)

```bash
# Pull reverted code
git pull origin comp_old&new

# Install dependencies (if needed)
uv sync

# Run database migration rollback (if needed)
uv run alembic downgrade -1

# Restart application
systemctl restart fastapi-app

# Or redeploy with script
./scripts/deploy.sh --version v1.0.0
```

#### 3. Health Check (2 minutes)

```bash
# Run health checks
./scripts/health-check.sh

# Run smoke tests
./scripts/smoke-test.sh http://localhost:8000

# Verify critical endpoints
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/users/me
```

#### 4. Monitor & Validate (2 minutes)

```bash
# Monitor metrics
./scripts/monitor.sh --duration 120

# Check logs for errors
tail -f /var/log/fastapi/app.log | grep ERROR
```

### Validation

- ✅ Code reverted to previous version
- ✅ Application restarted successfully
- ✅ All health checks passing
- ✅ Metrics back to normal
- ✅ No errors in logs

---

## Rollback Option 4: Database Restore

**Time:** < 30 minutes  
**Risk:** HIGH (possible data loss)  
**Use When:** Data corruption, catastrophic failure

### ⚠️ WARNING

**This is a LAST RESORT option. Only use if:**
- Data corruption detected
- Database schema incompatible
- No other rollback options work
- Approved by CTO/Engineering Lead

**Data Loss Risk:**
- All data written since backup will be lost
- User sessions will be invalidated
- In-flight transactions will be lost

### Prerequisites

- Database backup file available
- Database admin access
- Downtime window approved
- Users notified of data loss risk

### Steps

#### 1. Enable Maintenance Mode (2 minutes)

```bash
# Stop application
systemctl stop fastapi-app

# Set maintenance mode
export MAINTENANCE_MODE=true

# Update status page
curl -X POST https://status.example.com/incidents \
  -d '{"status": "maintenance", "message": "Emergency maintenance in progress"}'
```

#### 2. Create Current State Backup (5 minutes)

```bash
# Even if corrupted, backup current state
pg_dump -h $DB_HOST -U $DB_USER -d $DB_NAME > emergency-backup-$(date +%Y%m%d-%H%M%S).sql

# Verify backup created
ls -lh emergency-backup-*.sql
```

#### 3. Restore from Backup (15 minutes)

```bash
# Terminate existing connections
psql -h $DB_HOST -U $DB_USER -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB_NAME';"

# Drop database (DANGER!)
psql -h $DB_HOST -U $DB_USER -c "DROP DATABASE IF EXISTS $DB_NAME;"

# Recreate database
psql -h $DB_HOST -U $DB_USER -c "CREATE DATABASE $DB_NAME;"

# Restore from backup
psql -h $DB_HOST -U $DB_USER -d $DB_NAME < backup-YYYYMMDD-HHMMSS.sql

# Verify restore
psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT count(*) FROM users;"
```

#### 4. Restart Application (3 minutes)

```bash
# Start application
systemctl start fastapi-app

# Disable maintenance mode
export MAINTENANCE_MODE=false

# Run health checks
./scripts/health-check.sh
```

#### 5. Validate & Notify (5 minutes)

```bash
# Run full test suite
./scripts/smoke-test.sh

# Check critical functionality
./scripts/critical-path-test.sh

# Notify users
# - Email to all users about data loss
# - Post incident report
# - Update status page
```

### Validation

- ✅ Database restored from backup
- ✅ Application running
- ✅ All critical functionality working
- ✅ Data integrity verified
- ✅ Users notified

---

## Post-Rollback Actions

### Immediate (< 30 minutes)

1. **Update Status Page**
   ```
   Status: Operational
   Message: "Previous deployment rolled back due to [REASON]. Service is stable."
   ```

2. **Notify Team**
   ```
   #engineering: "Deployment rolled back. Service stable. Root cause analysis pending."
   ```

3. **Create Incident**
   ```
   Priority: P1
   Title: "Production rollback - [DATE] deployment"
   Owner: [DEPLOYMENT_LEAD]
   ```

### Follow-Up (< 24 hours)

1. **Root Cause Analysis**
   - What went wrong?
   - Why wasn't it caught in testing?
   - How can we prevent this?

2. **Post-Mortem Document**
   - Timeline of events
   - Impact assessment
   - Lessons learned
   - Action items

3. **Update Tests**
   - Add tests for missed scenario
   - Improve pre-deployment validation
   - Enhance monitoring

### Long-Term (< 1 week)

1. **Fix Issues**
   - Address root cause
   - Re-test fix in staging
   - Plan re-deployment

2. **Improve Process**
   - Update deployment checklist
   - Enhance rollback procedures
   - Add monitoring gaps

3. **Team Training**
   - Share lessons learned
   - Update runbooks
   - Practice rollback procedures

---

## Rollback Decision Tree

```
                    Issue Detected
                          |
                Is it critical? ----------------> NO ---> Monitor closely
                          |                              Consider fix forward
                         YES
                          |
                    What's broken?
           _______________|_______________
          |               |               |
     Error rate     Response time    Specific feature
          |               |               |
     > 1%?          > 500ms?         Breaking?
          |               |               |
         YES             YES             YES
          |               |               |
    [INSTANT TRAFFIC SWITCH]        [DISABLE FEATURE FLAG]
          |                                   |
    Blue-Green available?               Flag exists?
       |           |                      |        |
      YES         NO                     YES      NO
       |           |                      |        |
   [OPTION 1] [OPTION 3]            [OPTION 2] [OPTION 3]
       |           |                      |        |
    < 5 min    < 15 min                < 2 min  < 15 min
```

---

## Testing Rollback Procedures

### Staging Rollback Test

**Frequency:** Before every major deployment

```bash
# 1. Deploy to staging
./scripts/deploy-staging.sh

# 2. Simulate failure
curl -X POST http://staging/api/v1/_testing/simulate-error-spike

# 3. Execute rollback
./scripts/rollback-staging.sh

# 4. Verify staging healthy
./scripts/health-check-staging.sh
```

### Production Rollback Drill

**Frequency:** Quarterly

1. **Schedule drill**
   - Notify team 1 week in advance
   - Use low-traffic time window
   - Prepare mock scenario

2. **Execute drill**
   - Deploy new version
   - Trigger planned rollback
   - Measure time to recovery

3. **Review results**
   - Document execution time
   - Identify bottlenecks
   - Update procedures

---

## Rollback Scripts

### Quick Rollback Script

```bash
#!/bin/bash
# scripts/quick-rollback.sh

set -e

echo "🔄 Starting quick rollback..."

# Switch traffic to blue
./scripts/traffic-split.sh --green 0 --blue 100

# Verify
if curl -s http://localhost:8000/api/v1/health | grep -q "ok"; then
    echo "✅ Rollback complete - Blue environment healthy"
else
    echo "❌ Rollback failed - manual intervention required"
    exit 1
fi

# Monitor for 5 minutes
./scripts/monitor.sh --duration 300
```

### Full Rollback Script

```bash
#!/bin/bash
# scripts/full-rollback.sh

set -e

VERSION=${1:-v1.0.0}

echo "🔄 Starting full rollback to $VERSION..."

# Revert code
git fetch --tags
git checkout $VERSION

# Install dependencies
uv sync

# Rollback database (if needed)
read -p "Rollback database migrations? (y/N) " -n 1 -r
if [[ $REPLY =~ ^[Yy]$ ]]; then
    uv run alembic downgrade -1
fi

# Restart application
systemctl restart fastapi-app

# Health check
./scripts/health-check.sh

echo "✅ Full rollback complete"
```

---

## Contact & Escalation

### On-Call Engineer
- Primary: Check PagerDuty rotation
- Backup: Check on-call schedule

### Escalation Path
1. **On-Call Engineer** (first responder)
2. **Engineering Lead** (if > 15 min)
3. **CTO** (if > 30 min or critical)

### Emergency Contacts
```
On-Call: pagerduty@company.com
Lead Engineer: lead@company.com
CTO: cto@company.com
Emergency Hotline: +1-XXX-XXX-XXXX
```

---

## Appendix

### A. Common Rollback Scenarios

**Scenario 1: New endpoint returns 500 errors**
- **Solution:** Disable feature flag for that endpoint
- **Time:** < 2 minutes
- **Impact:** Only new endpoint affected

**Scenario 2: Overall error rate spiked to 2%**
- **Solution:** Instant traffic switch to blue
- **Time:** < 5 minutes
- **Impact:** All users see old version

**Scenario 3: Database migration broke queries**
- **Solution:** Full code revert + migration rollback
- **Time:** < 15 minutes
- **Impact:** All users see old version

**Scenario 4: Data corruption detected**
- **Solution:** Database restore from backup
- **Time:** < 30 minutes
- **Impact:** Data loss since backup

### B. Rollback Metrics

Track these metrics for each rollback:

- Time to detection (alert to acknowledgment)
- Time to decision (acknowledgment to rollback start)
- Time to execute (rollback start to completion)
- Total downtime (if any)
- User impact (# of affected users)

### C. Rollback Communication Template

```
Subject: Production Rollback - [DATE]

Team,

We executed a rollback of the deployment at [TIME].

Reason: [Brief description]
Method: [Rollback option used]
Duration: [X minutes]
Impact: [User impact description]

Current Status: [Stable/Investigating]

Next Steps:
1. [Action item 1]
2. [Action item 2]

Post-mortem: [Link to document]

Questions? Reply to this thread.
```

---

**Remember:** Rollback is not failure. It's a safety mechanism. Better to rollback quickly than to debug in production.

**Practice makes perfect:** Regular rollback drills ensure smooth execution during real incidents.

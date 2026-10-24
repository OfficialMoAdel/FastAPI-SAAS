# Dashboard Module AGENTS.md

## Package Identity
Read-only aggregation facade for dashboard metrics (users, revenue, activity).
Owns no database tables — aggregates other modules' models in a single
snapshot query. Follows the read-only module pattern (no `models.py`).

## Setup & Run
```bash
# Type check
uv run mypy src/app/modules/dashboard/

# Lint
uv run ruff check src/app/modules/dashboard/
```

## Patterns & Conventions

### Code Organization
- ? DO: Keep this module read-only — no writes, no owned tables
- ? DO: Scope every subquery by `tenant_id` (tenant isolation is structural here)
- ? DO: Use the sanctioned read-only seam: `audit` / `billing` / `users` models
- ? DON'T: Import other modules' services — aggregate their models directly

### Aggregation Pattern
```python
# Example from src/app/modules/dashboard/service.py:

async def _get_all_metrics(self, db, start, end_date, tenant_id, ...):
    """Fetch all 11 aggregates in ONE statement (one snapshot, one RTT)."""
    audit = self._audit_grouped_subquery(start, end_date, tenant_id)
    columns = [
        *self._user_subqueries(start, end_date, tenant_id, audit=audit),
        *self._revenue_subqueries(start, end_date, tenant_id),
        audit.c.logins,
        audit.c.api_calls,
        audit.c.failed_auth,
        audit.c.webhook_deliveries,
    ]
    async with db_circuit_guard():
        result = await db.execute(select(*columns))
```

### DO: Read `audit_logs` once per statement, with conditional aggregates

Five dashboard aggregates read the SAME `audit_logs` row set — `tenant_id = X
AND created_at BETWEEN start AND end`:

| aggregate | predicate |
|---|---|
| `logins` | `action IN ('auth.login', 'auth.sso_login')` |
| `api_calls` | *(none — the unfiltered superset)* |
| `failed_auth` | `action = 'auth.login' AND coalesce(details->>'success','true') = 'false'` |
| `webhook_deliveries` | `action LIKE 'webhook.%'` |
| `active_users` | `count(DISTINCT user_id)` over the `logins` predicate |

They were five separate scalar subqueries, so one window was scanned five
times — four of them probing `idx_audit_tenant_action` and then fetching the
heap for `details`/`user_id`. `_audit_grouped_subquery` makes them five
`count(...) FILTER (WHERE ...)` aggregates over ONE pass:

```python
# ONE scan serving all five
select(
    func.count(AuditLog.id).filter(login_actions).label("logins"),
    func.count(AuditLog.id).label("api_calls"),
    func.count(AuditLog.id).filter(failed_auth).label("failed_auth"),
    func.count(AuditLog.id).filter(AuditLog.action.like("webhook.%")).label("webhook_deliveries"),
    func.count(func.distinct(AuditLog.user_id)).filter(login_actions).label("active_users"),
).where(criterion(AuditLog, tenant_id), AuditLog.created_at >= start, AuditLog.created_at <= end_date).subquery()
```

The access path is `idx_audit_logs_tenant_created_id (tenant_id, created_at,
id)` — its leading two columns are exactly the window predicate, so the
planner range-scans the window instead of probing the action index five
times. The subquery is an aggregate with **no GROUP BY**, so it yields
exactly one row (all zeros for an empty window), which keeps the merged
statement's `result.one()` valid.

**Keep the predicates byte-identical when editing either side.** The scalar
subqueries still exist for the per-group getters, and the parity between the
two spellings is pinned by
`tests/unit/modules/dashboard/test_p4_audit_grouped_scan.py`.

### Caching
- `get_metrics` caches per `(tenant_id, time_range)` via `cache_client`
- Cache key: `cache_client.tenant_key(f"dashboard:metrics:{time_range.value}", tenant_id)`
- A cold (or just-expired) key is **single-flighted** (P2-4): the first caller
  claims `{cache_key}:refresh` with `cache_client.set_nx` and computes; the
  others poll the key and are served the stored payload. A waiter that
  outlives `_METRICS_REFRESH_WAIT_SECONDS` (5s) computes anyway rather than
  stalling the dashboard. The lock is the adapter's own `set_nx` +
  `release_lock_if_owned` pair, so the documented `get_json`/`set_json` seam
  and the stored payload type are unchanged. The compute half lives in
  `_compute_metrics`, which `get_metrics` calls inside the lock.

### Time Ranges
- `today` / `week` / `month` (default) / `quarter` / `year` / `all`
- `TimeRange.ALL` folds the min-created lookup into the merged statement

## Touch Points / Key Files

- Service: `src/app/modules/dashboard/service.py` - aggregation + caching
- Schemas: `src/app/modules/dashboard/schemas.py` - `UserMetrics`, `RevenueMetrics`, `ActivityMetrics`, `DashboardMetricsResponse`
- Router: `src/app/modules/dashboard/router.py` - `GET /dashboard?time_range=`

## JIT Index Hints

- Find subqueries: `rg -n "_subqueries" src/app/modules/dashboard/service.py`
- Find endpoints: `rg -n "@router\.(get|post|put|delete)" src/app/modules/dashboard/`

## Common Gotchas

- Every aggregate must be tenant-scoped — a missing `tenant_id` filter here leaks cross-tenant metrics
- Column layout is positional (`[4 user | 3 revenue | 4 activity]`); keep `_row_to_*` offsets in sync when adding aggregates
- Single-statement snapshot: all metrics reflect one point in time

## Pre-PR Checks

```bash
uv run mypy src/app/modules/dashboard/ && uv run ruff check src/app/modules/dashboard/
```
